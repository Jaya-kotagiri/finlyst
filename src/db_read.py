"""Read one verified PostgreSQL snapshot without importing or consulting financial files.

Source observations supply complete original meaning and provenance. Typed SQL rows
and relational links must agree with that snapshot before any financial view is returned.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import re
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

from . import db, db_import, identities


VIEWS = ('transactions', 'obligations', 'relationships', 'settlements')
STATUSES = ('OPEN', 'PARTIAL', 'CLOSED', 'UNASSESSED')


def _uuid(value, label):
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f'{label} must be a valid UUID') from None


@contextmanager
def _read_only(connection):
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Database queries require an idle connection; finish caller work first')
    try:
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            yield
    except psycopg.Error as exc:
        raise ValueError(f'Database query failed: {type(exc).__name__} '
                         f'(SQLSTATE {exc.sqlstate or "unavailable"}); no data changed') from None


def list_snapshots(connection):
    """List explicit snapshot choices; never infer a latest or current financial view."""
    with _read_only(connection):
        rows = connection.execute('''SELECT i.snapshot_id,i.imported_at,r.record_kind,count(r.record_id)
            FROM finlyst.imported_snapshots i LEFT JOIN finlyst.snapshot_records r USING (snapshot_id)
            GROUP BY i.snapshot_id,i.imported_at,r.record_kind
            ORDER BY i.imported_at,i.snapshot_id,r.record_kind''').fetchall()
    result = {}
    for identifier, imported_at, kind, count in rows:
        key = str(identifier)
        item = result.setdefault(key, dict(snapshot_id=key, imported_at=imported_at.isoformat(), counts={}))
        if kind is not None:
            item['counts'][kind] = count
    return list(result.values())


def _verify_identity_rows(connection, plan):
    """Verify selected relational identity metadata against the retained registry."""
    snapshot = plan['snapshot_id']
    records = {row['record_id'] for row in plan['rows']['snapshot_records']}
    revisions = {row['revision_id'] for row in plan['rows']['snapshot_records']}
    mappings = {row['mapping_id'] for row in plan['rows']['snapshot_decisions']}
    sources = {row['source_id'] for row in plan['rows']['snapshot_sources']}
    scopes = {
        'durable_records': ('record_id', records, 'SELECT record_id FROM finlyst.snapshot_records WHERE snapshot_id=%s'),
        'interpretation_revisions': ('revision_id', revisions, 'SELECT revision_id FROM finlyst.snapshot_records WHERE snapshot_id=%s'),
        'legacy_mappings': ('mapping_id', mappings, 'SELECT mapping_id FROM finlyst.snapshot_decisions WHERE snapshot_id=%s'),
    }
    for table, (key, selected, scope) in scopes.items():
        expected = [row for row in plan['rows'][table] if row[key] in selected]
        columns = tuple(expected[0]) if expected else db_import.KEYS[table]
        query = sql.SQL('SELECT {} FROM finlyst.{} WHERE {} IN (').format(
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table), sql.Identifier(key)) + sql.SQL(scope + ')')
        actual = connection.execute(query, (snapshot,)).fetchall()
        index = columns.index(key)
        if {row[index]: row for row in actual} != {row[key]: tuple(row[column] for column in columns) for row in expected}:
            raise ValueError(f'Database snapshot identity mismatch in {table}')
    # These sets may legitimately gain evidence/locations on a later import. Earlier
    # snapshots require their own evidence to remain present, not removal of additions.
    evidence_scopes = {
        'identity_keys': ('record_id', records, scopes['durable_records'][2]),
        'mapping_evidence': ('mapping_id', mappings, scopes['legacy_mappings'][2]),
        'source_locations': ('source_id', sources, 'SELECT source_id FROM finlyst.snapshot_sources WHERE snapshot_id=%s'),
        'source_dependencies': ('source_id', sources, 'SELECT source_id FROM finlyst.snapshot_sources WHERE snapshot_id=%s'),
    }
    for table, (key, selected, scope) in evidence_scopes.items():
        expected = [row for row in plan['rows'][table] if row[key] in selected]
        columns = db_import.KEYS[table]
        query = sql.SQL('SELECT {} FROM finlyst.{} WHERE {} IN (').format(
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table), sql.Identifier(key)) + sql.SQL(scope + ')')
        actual = set(connection.execute(query, (snapshot,)).fetchall())
        if not {tuple(row[column] for column in columns) for row in expected} <= actual:
            raise ValueError(f'Database snapshot provenance is missing from {table}')


def read_snapshot(connection, snapshot_id):
    """Recover and validate original financial documents solely from PostgreSQL.

    Repeatable-read isolation keeps the registry, sources, typed revisions and links
    from being read at different database moments. No filesystem source is required.
    """
    identifier = _uuid(snapshot_id, 'snapshot_id')
    with _read_only(connection):
        stored = connection.execute('''SELECT manifest,registry_schema_version,registry_payload
            FROM finlyst.imported_snapshots WHERE snapshot_id=%s''', (identifier,)).fetchone()
        if stored is None:
            raise ValueError('Snapshot was not found; choose an ID from db-snapshots')
        manifest, registry_version, registry = stored
        if not isinstance(registry, dict) or registry.get('schema_version') != registry_version:
            raise ValueError('Stored snapshot registry schema version is inconsistent')
        if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'sources', 'registry_sha256'}
                or manifest['schema_version'] != '3D.2.1' or not isinstance(manifest['sources'], dict)
                or manifest['registry_sha256'] != identities.fingerprint(registry)):
            raise ValueError('Stored snapshot manifest version or registry fingerprint is invalid')
        rows = connection.execute('''SELECT s.role,s.source_id,a.role,a.content_sha256,a.document_sha256,
                    d.schema_version,d.payload
            FROM finlyst.snapshot_sources s
            JOIN finlyst.source_artifacts a USING (source_id)
            JOIN finlyst.source_documents d USING (source_id)
            WHERE s.snapshot_id=%s ORDER BY s.role''', (identifier,)).fetchall()
        sources, docs, metadata = {}, {}, {}
        for role, source_id, source_role, byte_hash, doc_hash, version, payload in rows:
            if (source_role != role.split(':')[0] or doc_hash != identities.fingerprint(payload)
                    or version != (payload.get('schema_version') if isinstance(payload, dict) else None)):
                raise ValueError('Stored source document hash, role or schema version is inconsistent')
            sources[role], docs[role] = str(source_id), payload
            metadata[role] = dict(content_sha256=byte_hash)
        if sources != manifest['sources']:
            raise ValueError('Snapshot source documents or membership are missing or inconsistent')
        banks = sorted(role.split(':', 1)[1] for role in sources if role.startswith('bank:'))
        expected_roles = {prefix + bank for bank in banks for prefix in ('bank:', 'events:', 'reconciled:')}
        expected_roles.update(('relationships', 'classification', 'coverage', 'origins', 'settlements'))
        if not banks or set(docs) != expected_roles:
            raise ValueError('Snapshot must contain complete bank, event and downstream source roles')
        snapshot = dict(banks={bank: docs['bank:' + bank] for bank in banks},
            events={bank: docs['events:' + bank] for bank in banks},
            reconciled=[docs['reconciled:' + bank] for bank in banks],
            **{key: docs[key] for key in ('relationships', 'classification', 'coverage', 'origins', 'settlements')})
        # Do not reinterpret stored history. JSONB key ordering differs from original
        # JSON; its canonical source hashes and original import manifest bind values.
        plan = db_import._projection_plan(snapshot, registry, metadata)
        if plan['snapshot_id'] != identifier or plan['rows']['imported_snapshots'][0]['manifest'] != manifest:
            raise ValueError('Stored snapshot identity differs from its source manifest')
        db_import.verify_plan(connection, plan)
        _verify_identity_rows(connection, plan)
    return dict(snapshot_id=str(identifier), snapshot=snapshot, registry=registry, sources=sources)


def _filters(view, bank, date_from, date_to, status, record_id):
    if view not in VIEWS:
        raise ValueError('Unknown database view; choose ' + ', '.join(VIEWS))
    if bank is not None and bank not in ('kotak', 'sbi'):
        raise ValueError('bank must be kotak or sbi')
    for label, value in (('date_from', date_from), ('date_to', date_to)):
        if value is not None:
            try:
                if not isinstance(value, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                raise ValueError(f'{label} must be a calendar date in YYYY-MM-DD format') from None
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError('date_from must not be after date_to')
    if status is not None and (view not in ('obligations', 'settlements') or status not in STATUSES):
        raise ValueError('status applies only to obligations/settlements and must be ' + ', '.join(STATUSES))
    if record_id is not None:
        _uuid(record_id, 'record_id')


def read_view(connection, snapshot_id, view, *, bank=None, date_from=None, date_to=None, status=None, record_id=None):
    """Return unchanged financial meanings with durable identities and provenance.

    The settlement effect remains separate from base classification: an allocation
    applies only to its verified amount, not automatically to the whole bank movement.
    """
    _filters(view, bank, date_from, date_to, status, record_id)
    bundle = read_snapshot(connection, snapshot_id)
    snapshot, registry, sources = (bundle[key] for key in ('snapshot', 'registry', 'sources'))
    selected_record = str(_uuid(record_id, 'record_id')) if record_id is not None else None
    provenance = {}
    for role, kind, legacy, payload in db_import.observations(snapshot):
        mapping_id = identities.observation_id(sources[role], kind, legacy)
        decision = registry['legacy_mappings'][mapping_id]['decisions'][-1]
        provenance.setdefault(decision['durable_id'], []).append(dict(mapping_id=mapping_id,
            source_id=sources[role], role=role, legacy_id=legacy, payload=payload, decision=decision))
    for values in provenance.values():
        values.sort(key=lambda row: (row['role'], row['mapping_id']))

    def identity(role, kind, legacy):
        decision = identities.lookup(registry, sources[role], kind, legacy)
        return dict(record_id=decision['durable_id'], revision_id=decision['revision_id'],
                    legacy_id=legacy, source_id=sources[role])

    def wrapper(role, kind, legacy, payload):
        return dict(identity(role, kind, legacy), data=payload)

    def matches_bank_date(transaction):
        day = transaction['transaction_date']
        return ((bank is None or transaction['bank'].lower() == bank)
                and (date_from is None or day >= date_from) and (date_to is None or day <= date_to))

    transactions = {row['transaction_id']: row for rows in snapshot['banks'].values() for row in rows}
    classifications = {row['transaction_id']: row for row in snapshot['classification']['transactions']}
    meanings = {row['bank_transaction']['transaction_id']: row['event']
                for doc in snapshot['reconciled'] for row in doc['transactions']}
    effects = {row['transaction_id']: row for row in snapshot['settlements']['classification_effects']}
    relationships = snapshot['relationships']['relationships']
    obligations = snapshot['origins']['obligations']
    settlements = {row['obligation_id']: row for row in snapshot['settlements']['obligation_settlement_view']}
    allocations = {row['allocation_id']: wrapper('settlements', 'allocation', row['allocation_id'], row)
                   for row in snapshot['settlements']['allocations']}
    records = []
    if view == 'transactions':
        for transaction in sorted(transactions.values(), key=lambda row: (row['transaction_date'], row['bank'], row['transaction_id'])):
            if not matches_bank_date(transaction):
                continue
            legacy = transaction['transaction_id']
            linked = [wrapper('relationships', 'relationship', row['relationship_id'], row) for row in relationships
                      if legacy in row['transaction_ids'] or legacy in row['candidate_transaction_ids']]
            records.append(dict(identity('bank:' + transaction['bank'].lower(), 'transaction', legacy),
                bank_transaction=transaction, event=meanings[legacy], classification=classifications[legacy],
                settlement_effect=effects.get(legacy), relationships=linked))
    elif view == 'relationships':
        for row in relationships:
            # An unresolved candidate is not an executed relationship participant.
            if any(value is not None for value in (bank, date_from, date_to)) and not any(
                    matches_bank_date(transactions[key]) for key in row['transaction_ids']):
                continue
            records.append(wrapper('relationships', 'relationship', row['relationship_id'], row))
    else:
        for origin in obligations:
            legacy = origin['obligation_id']
            settlement = settlements[legacy]
            if status is not None and settlement['status'] != status:
                continue
            if bank is not None and not any(transactions[key]['bank'].lower() == bank for key in origin['originating_transaction_ids']):
                continue
            if ((date_from is not None and origin['origin_date'] < date_from)
                    or (date_to is not None and origin['origin_date'] > date_to)):
                continue
            if view == 'obligations':
                records.append(dict(identity('origins', 'obligation', legacy), obligation=origin, settlement=settlement,
                                    allocations=[allocations[key] for key in settlement['allocation_ids']]))
            else:
                records.append(wrapper('settlements', 'settlement', legacy, settlement))
    records = [dict(row, provenance=provenance.get(row['record_id'], [])) for row in records
               if selected_record is None or row['record_id'] == selected_record]
    return deepcopy(dict(schema_version='3D.3.1', snapshot_id=bundle['snapshot_id'], view=view,
                         count=len(records), records=records))


def run(root=db.ROOT, *, view='snapshots', snapshot_id=None, bank=None, date_from=None, date_to=None, status=None, record_id=None):
    if view != 'snapshots':
        _uuid(snapshot_id, 'snapshot_id')
        _filters(view, bank, date_from, date_to, status, record_id)
    elif any(value is not None for value in (snapshot_id, bank, date_from, date_to, status, record_id)):
        raise ValueError('Snapshot listing does not accept financial record filters')
    with db.connect(root) as connection:
        if view == 'snapshots':
            return list_snapshots(connection)
        return read_view(connection, snapshot_id, view, bank=bank, date_from=date_from, date_to=date_to,
                         status=status, record_id=record_id)
