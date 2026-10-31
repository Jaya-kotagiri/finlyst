"""Explicit, append-only PostgreSQL import of validated financial snapshots.

Financial derivation and durable identity decisions remain in the existing pipeline.
This module validates those decisions, stores revisions, and records coherent snapshot
membership. It never replaces an interpretation or writes a source JSON file.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from . import db, identities
from .db_projection import project
from .identity_models import Registry
from .schema_validation import parse_document


IMPORT_LOCK = 741139310002
# Dependency order also makes every failed import one atomic rollback.
KEYS = {
    'durable_records': ('record_id',),
    'identity_keys': ('record_id', 'identity_key'),
    'source_artifacts': ('source_id',),
    'source_documents': ('source_id',),
    'source_locations': ('source_id', 'location'),
    'source_dependencies': ('source_id', 'upstream_path', 'content_sha256'),
    'interpretation_revisions': ('revision_id',),
    'revision_references': ('revision_id', 'role', 'target_record_id'),
    'legacy_mappings': ('mapping_id',),
    'mapping_evidence': ('mapping_id', 'identity_key'),
    'identity_decisions': ('mapping_id', 'decision_order'),
    'decision_references': ('mapping_id', 'decision_order', 'role', 'target_record_id'),
    'decision_candidates': ('mapping_id', 'decision_order', 'candidate_id'),
    'source_observations': ('mapping_id',),
    'accounts': ('revision_id',),
    'bank_transactions': ('revision_id',),
    'semantic_events': ('revision_id',),
    'relationships': ('revision_id',),
    'relationship_transactions': ('relationship_revision_id', 'transaction_id', 'role'),
    'effective_classifications': ('source_document_id', 'transaction_id'),
    'classification_relationships': ('source_document_id', 'transaction_id', 'relationship_id', 'role'),
    'obligations': ('revision_id',),
    'obligation_links': ('obligation_revision_id', 'role', 'target_record_id'),
    'settlements': ('revision_id',),
    'allocations': ('revision_id',),
    'settlement_allocations': ('settlement_revision_id', 'allocation_record_id', 'allocation_revision_id'),
    'imported_snapshots': ('snapshot_id',),
    'snapshot_sources': ('snapshot_id', 'role'),
    'snapshot_records': ('snapshot_id', 'record_id'),
    'snapshot_decisions': ('snapshot_id', 'mapping_id'),
}
JSON_COLUMNS = {'payload', 'unresolved_references', 'continuity', 'context', 'financial_treatment',
                'evidence', 'details', 'semantic', 'provenance', 'manifest', 'registry_payload'}


def documents(snapshot):
    """The eleven source roles shared with the existing identity registry."""
    result = {f'bank:{bank}': rows for bank, rows in snapshot['banks'].items()}
    result.update({f'events:{bank}': doc for bank, doc in snapshot['events'].items()})
    result.update({f'reconciled:{doc["bank"].lower()}': doc for doc in snapshot['reconciled']})
    result.update({role: snapshot[role] for role in ('relationships', 'classification', 'coverage', 'origins', 'settlements')})
    return result


def observations(snapshot):
    """Original source-scoped records, including projection aliases and review items."""
    result = []
    for bank, transactions in sorted(snapshot['banks'].items()):
        accounts = {}
        for item in transactions:
            result.append(('bank:' + bank, 'transaction', item['transaction_id'], item))
            if item['account'] is not None:
                accounts[item['bank'] + ':' + item['account']] = {'bank': item['bank'], 'account': item['account']}
        result.extend(('bank:' + bank, 'account', key, item) for key, item in sorted(accounts.items()))
    for bank, doc in sorted(snapshot['events'].items()):
        result.extend(('events:' + bank, 'event', item['event_id'], item) for item in doc['events'])
    for doc in snapshot['reconciled']:
        role = 'reconciled:' + doc['bank'].lower()
        for item in doc['transactions']:
            result.append((role, 'transaction', item['bank_transaction']['transaction_id'], item))
            if item['event']:
                result.append((role, 'event', item['event']['event_id'], item['event']))
    for role in ('classification', 'coverage'):
        result.extend((role, 'transaction', item['transaction_id'], item) for item in snapshot[role]['transactions'])
    for role, field, kind, key in (
        ('relationships', 'relationships', 'relationship', 'relationship_id'),
        ('origins', 'obligations', 'obligation', 'obligation_id'),
        ('origins', 'clarification_candidates', 'origin_question', 'clarification_id'),
        ('settlements', 'allocations', 'allocation', 'allocation_id'),
        ('settlements', 'obligation_settlement_view', 'settlement', 'obligation_id'),
        ('settlements', 'unresolved_candidates', 'candidate', 'candidate_id'),
        ('settlements', 'clarification_candidates', 'settlement_question', 'clarification_id'),
    ):
        result.extend((role, kind, item[key], item) for item in snapshot[role][field])
    return result


def prepare(snapshot, registry, metadata=None):
    """Validate and project before connecting/writing; never allocate new identities."""
    from .db_import_validation import validate_current_mappings
    from .integrity import audit_layers

    snapshot, registry, metadata = deepcopy(snapshot), deepcopy(registry), deepcopy(metadata or {})
    if identities.migrate(snapshot, registry, metadata=metadata) != registry:
        raise ValueError('Identity registry is missing current mappings or provenance; run and review the identities stage first')
    checks = audit_layers(*(snapshot[key] for key in ('banks', 'events', 'reconciled', 'relationships',
                          'classification', 'coverage', 'origins', 'settlements')))
    failed = [check['check'] for check in checks if check['status'] != 'PASS']
    if failed:
        raise ValueError('Database import integrity checks failed: ' + ', '.join(failed))
    docs = documents(snapshot)
    sources = {role: identities.source_id(role.split(':')[0], doc, metadata.get(role, {}).get('content_sha256'))
               for role, doc in docs.items()}
    validate_current_mappings(snapshot, registry, sources)
    rows = {table: [] for table in KEYS}
    for key, record in sorted(registry['records'].items()):
        rows['durable_records'].append(dict(record_id=UUID(key), record_kind=record['kind'], birth_seed=record['birth_seed']))
        rows['identity_keys'].extend(dict(record_id=UUID(key), identity_key=value) for value in record['identity_keys'])
    for key, source in sorted(registry['sources'].items()):
        rows['source_artifacts'].append(dict(source_id=UUID(key), **{k: source[k] for k in ('role', 'content_sha256', 'document_sha256')}))
        rows['source_locations'].extend(dict(source_id=UUID(key), location=value) for value in source['locations'])
        rows['source_dependencies'].extend(dict(source_id=UUID(key), upstream_path=path, content_sha256=value)
            for path, hashes in sorted(source['upstream_files'].items()) for value in hashes)
    for role, doc in sorted(docs.items()):
        rows['source_documents'].append(dict(source_id=UUID(sources[role]),
            schema_version=doc.get('schema_version') if isinstance(doc, dict) else None, payload=doc))
    for key, revision in sorted(registry['revisions'].items()):
        kind = registry['records'][revision['record_id']]['kind']
        rows['interpretation_revisions'].append(dict(revision_id=UUID(key), record_id=UUID(revision['record_id']),
            record_kind=kind, interpretation_sha256=revision['interpretation_sha256']))
        rows['revision_references'].extend(dict(revision_id=UUID(key), role=role, target_record_id=UUID(target))
            for role, targets in sorted(revision['references'].items()) for target in targets)
    for key, mapping in sorted(registry['legacy_mappings'].items()):
        common = dict(mapping_id=UUID(key))
        rows['legacy_mappings'].append(dict(common, source_id=UUID(mapping['source_id']), record_kind=mapping['kind'],
            legacy_id=mapping['legacy_id'], record_sha256=mapping['record_sha256']))
        rows['mapping_evidence'].extend(dict(common, identity_key=value) for value in mapping.get('identity_evidence', []))
        for order, decision in enumerate(mapping['decisions'], 1):
            base = dict(common, decision_order=order)
            rows['identity_decisions'].append(dict(base, record_kind=mapping['kind'], status=decision['status'],
                record_id=UUID(decision['durable_id']) if decision['durable_id'] else None,
                revision_id=UUID(decision['revision_id']) if decision['revision_id'] else None,
                reason=decision['reason'], unresolved_references=decision['unresolved_references'], continuity=decision['continuity']))
            rows['decision_references'].extend(dict(base, role=role, target_record_id=UUID(target))
                for role, targets in sorted(decision['references'].items()) for target in targets)
            rows['decision_candidates'].extend(dict(base, candidate_id=UUID(target), record_kind=mapping['kind'])
                for target in decision['candidate_ids'])
    manifest = dict(schema_version='3D.2.1', sources=sources, registry_sha256=identities.fingerprint(registry))
    snapshot_id = uuid5(NAMESPACE_URL, 'urn:finlyst:database-import:v1:' + identities.fingerprint(manifest))
    rows['imported_snapshots'].append(dict(snapshot_id=snapshot_id, manifest=manifest,
        registry_schema_version=registry['schema_version'], registry_payload=registry))
    rows['snapshot_sources'].extend(dict(snapshot_id=snapshot_id, role=role, source_id=UUID(source))
                                    for role, source in sorted(sources.items()))
    active = {}
    observed = set()
    for role, kind, legacy, payload in observations(snapshot):
        key = identities.observation_id(sources[role], kind, legacy)
        mapping = registry['legacy_mappings'].get(key)
        if mapping is None or mapping['record_sha256'] != identities.fingerprint(payload):
            raise ValueError(f'{role}: {kind} source observation differs from its identity mapping')
        decision = mapping['decisions'][-1]
        if decision['status'] != 'resolved':
            raise ValueError(f'{role}: {kind} {legacy} has an unresolved durable identity; review before import')
        if key in observed:
            raise ValueError('Duplicate source observation in import snapshot')
        observed.add(key)
        rows['source_observations'].append(dict(mapping_id=UUID(key), payload=payload))
        rows['snapshot_decisions'].append(dict(snapshot_id=snapshot_id, mapping_id=UUID(key), decision_order=len(mapping['decisions'])))
        record, revision = decision['durable_id'], decision['revision_id']
        if record in active and active[record] != revision:
            raise ValueError('One durable record has competing revisions within the current snapshot')
        active[record] = revision
    expected = {key for key, mapping in registry['legacy_mappings'].items() if mapping['source_id'] in sources.values()}
    if expected != observed:
        raise ValueError('Current source observations do not exactly cover the identity mappings')
    for record, revision in sorted(active.items()):
        if any(target not in active for targets in registry['revisions'][revision]['references'].values() for target in targets):
            raise ValueError('Active revision references a durable record outside its import snapshot')
        rows['snapshot_records'].append(dict(snapshot_id=snapshot_id, record_id=UUID(record), revision_id=UUID(revision),
            record_kind=registry['records'][record]['kind']))
    rows.update(project(snapshot, registry, sources))
    counts = {table: len(rows[table]) for table in ('accounts', 'bank_transactions', 'semantic_events', 'relationships',
              'effective_classifications', 'obligations', 'settlements', 'allocations', 'source_observations', 'snapshot_records')}
    return dict(rows=rows, snapshot_id=snapshot_id, counts=counts)


def _values(row):
    # Only confirmation_methods is a native PostgreSQL array; other lists are JSON.
    return [Jsonb(value) if key in JSON_COLUMNS and value is not None else value for key, value in row.items()]


def _insert_or_compare(connection, table, row):
    """Append or verify an identical immutable row. No UPDATE/DELETE/REPLACE path."""
    columns = tuple(row)
    keys = KEYS[table]
    query = sql.SQL('INSERT INTO finlyst.{} ({}) VALUES ({}) ON CONFLICT DO NOTHING').format(
        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)),
        sql.SQL(',').join(sql.Placeholder() for _ in columns))
    inserted = connection.execute(query, _values(row)).rowcount
    query = sql.SQL('SELECT {} FROM finlyst.{} WHERE {}').format(
        sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table),
        sql.SQL(' AND ').join(sql.SQL('{} = %s').format(sql.Identifier(key)) for key in keys))
    found = connection.execute(query, [row[key] for key in keys]).fetchone()
    if found != tuple(row.values()):
        # No SQL values/narration/account numbers are exposed in an error.
        raise ValueError(f'Immutable database conflict in {table}; existing data differs from the validated snapshot')
    return inserted


def verify_plan(connection, plan):
    """Compare complete active SQL rows/links with the source projection, not just totals.

    Historical rows may coexist. Both ends of allocation edges are selected from
    the same snapshot so revised evidence cannot double-count a repayment.
    """
    snapshot = plan['snapshot_id']
    active = 'SELECT revision_id FROM finlyst.snapshot_records WHERE snapshot_id = %s'
    classification = next(row['source_id'] for row in plan['rows']['snapshot_sources'] if row['role'] == 'classification')
    scopes = {table: ('revision_id IN (' + active + ')', [snapshot])
              for table in ('accounts', 'bank_transactions', 'semantic_events', 'relationships', 'obligations', 'settlements', 'allocations')}
    scopes.update({table: ('snapshot_id = %s', [snapshot])
                   for table in ('snapshot_sources', 'snapshot_records', 'snapshot_decisions')})
    scopes.update({table: ('source_document_id = %s', [classification])
                   for table in ('effective_classifications', 'classification_relationships')})
    scopes['relationship_transactions'] = ('relationship_revision_id IN (' + active + ')', [snapshot])
    scopes['obligation_links'] = ('obligation_revision_id IN (' + active + ')', [snapshot])
    scopes['revision_references'] = ('revision_id IN (' + active + ')', [snapshot])
    scopes['settlement_allocations'] = ('settlement_revision_id IN (' + active + ') AND allocation_revision_id IN (' + active + ')', [snapshot, snapshot])
    scopes['source_observations'] = ('mapping_id IN (SELECT mapping_id FROM finlyst.snapshot_decisions WHERE snapshot_id = %s)', [snapshot])
    scopes['source_documents'] = ('source_id IN (SELECT source_id FROM finlyst.snapshot_sources WHERE snapshot_id = %s)', [snapshot])
    scopes.update({table: ('(mapping_id, decision_order) IN (SELECT mapping_id, decision_order FROM finlyst.snapshot_decisions WHERE snapshot_id = %s)', [snapshot])
                   for table in ('identity_decisions', 'decision_references', 'decision_candidates')})
    active_revisions = {row['revision_id'] for row in plan['rows']['snapshot_records']}
    active_decisions = {(row['mapping_id'], row['decision_order']) for row in plan['rows']['snapshot_decisions']}
    counts = {}
    for table, (where, parameters) in scopes.items():
        expected = plan['rows'][table]
        if table == 'revision_references':
            expected = [row for row in expected if row['revision_id'] in active_revisions]
        if table in ('identity_decisions', 'decision_references', 'decision_candidates'):
            expected = [row for row in expected if (row['mapping_id'], row['decision_order']) in active_decisions]
        columns = tuple(expected[0]) if expected else KEYS[table]
        query = sql.SQL('SELECT {} FROM finlyst.{} WHERE ').format(
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table)) + sql.SQL(where)
        actual = connection.execute(query, parameters).fetchall()
        indices = [columns.index(key) for key in KEYS[table]]
        actual_by_key = {tuple(row[index] for index in indices): row for row in actual}
        expected_by_key = {tuple(row[key] for key in KEYS[table]): tuple(row[column] for column in columns) for row in expected}
        if actual_by_key != expected_by_key or len(actual) != len(expected):
            raise ValueError(f'Database snapshot verification failed in {table}; rows or links differ from validated source data')
        counts[table] = len(actual)
    return {table: counts[table] for table in plan['counts']}


def import_plan(connection, plan, *, before_commit=None):
    """Persist a freshly prepared plan atomically; the filesystem guard runs last."""
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Database import requires an idle connection; caller work must be committed or rolled back first')
    if db.status(connection)['pending']:
        raise ValueError('Database migrations pending; run python -m src.db migrate before importing')
    inserted = 0
    try:
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(%s)', (IMPORT_LOCK,))
            for table in KEYS:
                for row in plan['rows'][table]:
                    inserted += _insert_or_compare(connection, table, row)
            counts = verify_plan(connection, plan)
            if before_commit is not None:
                before_commit()
    except psycopg.Error as exc:
        raise ValueError(f'Database import rolled back: {type(exc).__name__} '
                         f'(SQLSTATE {exc.sqlstate or "unavailable"}); no financial rows committed') from None
    return dict(snapshot_id=str(plan['snapshot_id']), counts=counts, inserted_rows=inserted)


def import_snapshot(connection, snapshot, registry, *, metadata=None, before_commit=None):
    return import_plan(connection, prepare(snapshot, registry, metadata), before_commit=before_commit)


def _inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('input', 'output') for p in sorted((root / folder).rglob('*')) if p.is_file()}


def load_project(root=db.ROOT):
    """Capture source provenance and verify the existing read-only integrity report."""
    from .integrity import run as integrity

    root = Path(root).resolve()
    baseline = _inventory(root)

    def unchanged():
        if _inventory(root) != baseline:
            raise ValueError('Protected source files or identity registry changed during database import; import rolled back')

    report = integrity(root, write_output=False)
    if report['result'] != 'PASS' or report['failed_checks']:
        raise ValueError('Database import requires passing integrity checks: ' + ', '.join(report['failed_checks']))
    if report != json.loads((root / 'output/audit/financial_integrity.json').read_bytes()):
        raise ValueError('Saved integrity report is stale; validate the pipeline before importing')
    metadata = {}

    def read(name, role, upstream=()):
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise ValueError('Database source path must remain within the selected project root')
        raw = path.read_bytes()
        upstream_hashes = {}
        for upstream_name in upstream:
            source = (root / upstream_name).resolve()
            if not source.is_relative_to(root):
                raise ValueError('Upstream source path must remain within the selected project root')
            upstream_hashes[upstream_name] = hashlib.sha256(source.read_bytes()).hexdigest()
        metadata[role] = dict(location=name,
            content_sha256=hashlib.sha256(raw).hexdigest(), upstream_files=upstream_hashes)
        return json.loads(raw)

    coverage = read('output/audit/coverage_audit.json', 'coverage')
    banks, events, reconciled = {}, {}, []
    for bank in ('kotak', 'sbi'):
        inventory = coverage['sources'][bank]
        banks[bank] = read(f'output/bank/{bank}_transactions.json', 'bank:' + bank, [inventory['bank_file']])
        events[bank] = read(f'output/events/{bank}_events.json', 'events:' + bank, [inventory['notes_file']])
        reconciled.append(read(f'output/reconciled/{bank}_reconciled.json', 'reconciled:' + bank))
    snapshot = dict(banks=banks, events=events, reconciled=reconciled, coverage=coverage,
        relationships=read('output/relationships/relationships.json', 'relationships'),
        classification=read('output/classification/effective_transactions.json', 'classification'),
        origins=read('output/obligations/obligations.json', 'origins'),
        settlements=read('output/settlements/settlements.json', 'settlements'))
    path = root / 'output/identities/identity_registry.json'
    registry = parse_document(path.read_bytes(), Registry, str(path))
    unchanged()
    return snapshot, registry, metadata, unchanged


def run(root=db.ROOT):
    snapshot, registry, metadata, unchanged = load_project(root)
    plan = prepare(snapshot, registry, metadata)
    unchanged()
    with db.connect(root) as connection:
        return import_plan(connection, plan, before_commit=unchanged)
