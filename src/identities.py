"""Persistent durable identities, explicit legacy aliases and interpretation revisions.

No financial writer or matcher is changed. Keep this registry across imports.
Unknown identity produces an unresolved mapping, never a speculative second cashflow.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from .identity_models import Continuity, Registry
from .schema_validation import parse_document, validate_schema
from .validation import validate_transactions

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = uuid5(NAMESPACE_URL, 'urn:finlyst:durable-identities:v1')


def fingerprint(value):
    """Identity canonicalization v1: independent of JSON key order and whitespace."""
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def identifier(kind, value):
    return str(uuid5(NAMESPACE, kind + ':' + fingerprint(value)))


def observation_id(source_id, kind, legacy_id):
    return identifier('observation', [source_id, kind, legacy_id])


def empty_registry():
    return dict(schema_version='3R.2C.1', sources={}, records={}, revisions={}, legacy_mappings={})


def validate_registry(state):
    validate_schema(Registry, state, 'identity registry')
    for key, source in state['sources'].items():
        if key != identifier('source', [source['role'], source['content_sha256'], source['document_sha256']]):
            raise ValueError('identity registry: source identity mismatch')
    for key, record in state['records'].items():
        if key != identifier('record', [record['kind'], record['birth_seed']]):
            raise ValueError('identity registry: permanent record identity mismatch')
        if len(record['identity_keys']) != len(set(record['identity_keys'])):
            raise ValueError('identity registry: duplicate identity key')
    for key, revision in state['revisions'].items():
        if revision['record_id'] not in state['records'] or key != identifier('revision', revision):
            raise ValueError('identity registry: invalid revision identity/reference')
        if any(i not in state['records'] for ids in revision['references'].values() for i in ids):
            raise ValueError('identity registry: dangling revision reference')
    for key, mapping in state['legacy_mappings'].items():
        if mapping['source_id'] not in state['sources'] or key != observation_id(mapping['source_id'], mapping['kind'], mapping['legacy_id']):
            raise ValueError('identity registry: invalid legacy alias scope')
        for decision in mapping['decisions']:
            target, revision = decision['durable_id'], decision['revision_id']
            if decision['status'] == 'resolved':
                if target not in state['records'] or revision not in state['revisions']:
                    raise ValueError('identity registry: dangling legacy alias')
                if state['records'][target]['kind'] != mapping['kind'] or state['revisions'][revision]['record_id'] != target:
                    raise ValueError('identity registry: alias kind/revision mismatch')
                if decision['references'] != state['revisions'][revision]['references']:
                    raise ValueError('identity registry: alias/revision references disagree')
                if decision['unresolved_references']:
                    raise ValueError('identity registry: resolved alias has unresolved references')
            elif target is not None or revision is not None:
                raise ValueError('identity registry: unresolved identity cannot be counted as a record')
            for target in decision['candidate_ids']:
                if target not in state['records'] or state['records'][target]['kind'] != mapping['kind']:
                    raise ValueError('identity registry: invalid identity candidate')
            if any(i not in state['records'] for ids in decision['references'].values() for i in ids):
                raise ValueError('identity registry: dangling mapping reference')
            binding = decision['continuity']
            if binding and (decision['status'] != 'resolved' or
                            binding['target_id'] is not None and binding['target_id'] != decision['durable_id']):
                raise ValueError('identity registry: continuity decision disagrees with its target')
    return state


def _source(state, role, document, metadata=None):
    metadata = metadata or {}
    doc_hash = fingerprint(document)
    byte_hash = metadata.get('content_sha256', doc_hash)
    key = identifier('source', [role, byte_hash, doc_hash])
    source = state['sources'].setdefault(key, dict(role=role, content_sha256=byte_hash,
        document_sha256=doc_hash, locations=[], upstream_files={}))
    if metadata.get('location'):
        source['locations'] = sorted(set(source['locations'] + [metadata['location']]))
    for path, digest in metadata.get('upstream_files', {}).items():
        source['upstream_files'][path] = sorted(set(source['upstream_files'].get(path, []) + [digest]))
    return key


def source_id(role, document, content_sha256=None):
    """Compute scope before supplying an explicit continuity decision."""
    digest = fingerprint(document)
    return identifier('source', [role, content_sha256 or digest, digest])


def _record(state, kind, key, seed):
    target = identifier('record', [kind, seed])
    existing = state['records'].get(target)
    if existing is not None and (existing['kind'] != kind or existing['birth_seed'] != seed):
        raise ValueError('durable identity collision')
    state['records'].setdefault(target, dict(kind=kind, birth_seed=seed, identity_keys=[key]))
    return target


def _semantic(value):
    """Legacy links and source fingerprints are provenance, not interpretation identity."""
    if isinstance(value, list):
        return [_semantic(v) for v in value]
    if isinstance(value, dict):
        return {k: _semantic(v) for k, v in value.items()
                if not k.endswith(('_id', '_ids', '_sha256')) and k not in ('provenance', 'existing_relationship_context')}
    return value


def _observe(state, source, kind, legacy, payload, target, reason, references=None,
             candidates=(), unresolved=None, continuity=None, interpretation=None, identity_evidence=()):
    oid = observation_id(source, kind, legacy)
    references = {k: sorted(set(v)) for k, v in (references or {}).items()}
    revision_id = None
    if target is not None:
        revision = dict(record_id=target, interpretation_sha256=fingerprint(
            _semantic(payload) if interpretation is None else interpretation), references=references)
        revision_id = identifier('revision', revision)
        state['revisions'].setdefault(revision_id, revision)
    mapping = state['legacy_mappings'].setdefault(oid, dict(source_id=source, kind=kind,
        legacy_id=legacy, record_sha256=fingerprint(payload), decisions=[]))
    if mapping['record_sha256'] != fingerprint(payload):
        raise ValueError('source observation changed without a new source identity')
    mapping['identity_evidence'] = sorted(set(mapping.get('identity_evidence', []) + list(identity_evidence)))
    decision = dict(status='resolved' if target else 'unresolved', durable_id=target,
        revision_id=revision_id, reason=reason, candidate_ids=sorted(set(candidates)), references=references,
        unresolved_references=unresolved or {}, continuity=deepcopy(continuity))
    if not mapping['decisions'] or mapping['decisions'][-1] != decision:
        mapping['decisions'].append(decision)
    return target


def lookup(state, source, kind, legacy):
    """Aliases are always scoped to an artifact; never look up a bare legacy ID."""
    mapping = state['legacy_mappings'].get(observation_id(source, kind, legacy))
    return deepcopy(mapping['decisions'][-1]) if mapping else None


def _binding(state, oid, kind, key, bindings):
    binding = bindings.get(oid)
    if binding is None:
        return None
    validate_schema(Continuity, binding, 'identity continuity')
    target = binding['target_id']
    new_seed = fingerprint(['explicit_new', oid])
    requested = identifier('record', [kind, new_seed]) if target is None else target
    old = state['legacy_mappings'].get(oid)
    if old and old['decisions'][-1]['status'] == 'resolved' and requested != old['decisions'][-1]['durable_id']:
        raise ValueError('cannot reassign an already resolved source observation')
    if target is None:
        target = _record(state, kind, key, new_seed)
    elif target not in state['records'] or state['records'][target]['kind'] != kind:
        raise ValueError('continuity target is absent or belongs to another record kind')
    state['records'][target]['identity_keys'] = sorted(set(state['records'][target]['identity_keys'] + [key]))
    return target


def _bank(state, rows, source, bindings):
    validate_transactions(rows)
    if not rows:
        return {}
    if len({(r['bank'], r['account']) for r in rows}) != 1:
        raise ValueError('an identity bank import must describe one bank account')
    bank, account = rows[0]['bank'], rows[0]['account']
    account_id = None
    # Full account numbers from the inspected statements; masked/missing/short values need clarification.
    if account and re.fullmatch(r'[0-9]{8,}', account):
        key = fingerprint(['account', bank, account])
        account_id = _record(state, 'account', key, key)
        old = lookup(state, source, 'account', bank + ':' + account)
        if not old:
            _observe(state, source, 'account', bank + ':' + account,
                     {'bank': bank, 'account': account}, account_id, 'source_full_account_number')
    signatures = [fingerprint({k: v for k, v in row.items() if k != 'transaction_id'}) for row in rows]
    multiplicity = Counter(signatures)
    # Only records from earlier imports can supply an overlap match or conflict.
    prior = [m for m in state['legacy_mappings'].values() if m['kind'] == 'transaction' and m['source_id'] != source]
    result = {}
    for row, signature in zip(rows, signatures):
        legacy = row['transaction_id']; oid = observation_id(source, 'transaction', legacy)
        key = fingerprint(['transaction', account_id, signature])
        refs = {'account': [account_id]} if account_id else {}
        old = lookup(state, source, 'transaction', legacy)
        if old and old['status'] == 'resolved' and oid not in bindings:
            result[legacy] = old['durable_id']; continue
        candidates = [i for i, r in state['records'].items() if r['kind'] == 'transaction' and key in r['identity_keys']]
        # Hashes support overlap detection without copying private bank facts into the registry.
        coarse = fingerprint([account_id, row['transaction_date'], row['amount'], row['direction'], row['raw_description']])
        strong_ref = fingerprint([account_id, row['transaction_date'], row['direction'], row['reference']]) if row['reference'] else None
        conflicting = []
        for m in prior:
            d = m['decisions'][-1]
            if not d['durable_id']:
                continue
            r = state['records'][d['durable_id']]
            if key not in r['identity_keys'] and (fingerprint(['coarse', coarse]) in r['identity_keys'] or
                    strong_ref and fingerprint(['reference', strong_ref]) in r['identity_keys']):
                conflicting.append(d['durable_id'])
        target, reason = None, 'insufficient_bank_identity_evidence'
        if not account_id:
            if oid in bindings:
                raise ValueError('cannot bind a transaction without established account identity')
            reason = 'full_account_identity_required'
        elif oid in bindings:
            target = _binding(state, oid, 'transaction', key, bindings); reason = 'explicit_identity_continuity'
        elif (multiplicity[signature] > 1 or len(candidates) > 1 or any(
              key in m.get('identity_evidence', []) and m['decisions'][-1]['status'] == 'unresolved'
              and m['decisions'][-1]['reason'] == 'indistinguishable_bank_occurrences' for m in prior)):
            reason = 'indistinguishable_bank_occurrences'
        elif candidates:
            target, = candidates; reason = 'exact_account_bank_facts_overlap'
        elif conflicting:
            candidates = conflicting; reason = 'conflicting_or_incomplete_bank_identity_evidence'
        elif row['reference'] or row['balance_after'] is not None:
            target = _record(state, 'transaction', key, key); reason = 'unique_account_bank_facts'
        if target:
            additions = [fingerprint(['coarse', coarse])]
            if strong_ref: additions.append(fingerprint(['reference', strong_ref]))
            state['records'][target]['identity_keys'] = sorted(set(state['records'][target]['identity_keys'] + additions))
        result[legacy] = _observe(state, source, 'transaction', legacy, row, target, reason, refs,
            candidates=candidates if not target else (), continuity=bindings.get(oid),
            interpretation={k: v for k, v in row.items() if k != 'transaction_id'}, identity_evidence=[key])
    return result


def _finish(state, bindings):
    if any(key not in state['legacy_mappings'] for key in bindings):
        raise ValueError('continuity decision does not identify an observation in this registry')
    if any(state['legacy_mappings'][key]['decisions'][-1]['continuity'] != binding for key, binding in bindings.items()):
        raise ValueError('continuity decision was not applied')
    return validate_registry(state)


def import_bank(previous, rows, *, metadata=None, continuity=None):
    """Import another normalized statement without changing that statement or prior state."""
    validate_registry(previous)
    state = deepcopy(previous); bindings = continuity or {}
    source = _source(state, 'bank', rows, metadata)
    _bank(state, rows, source, bindings)
    return _finish(state, bindings), source


def _group(state, source, kind, items, bindings):
    """Resolve slots by durable parents; source labels can support claim continuity."""
    groups = defaultdict(list); results = {}
    for legacy, payload, anchors, refs, missing in items:
        key = fingerprint([kind, sorted(set(anchors))])
        groups[key].append((legacy, payload, refs, missing))
    for key, group in sorted(groups.items()):
        candidates = [i for i, r in state['records'].items() if r['kind'] == kind and key in r['identity_keys']]
        proposals = {}
        for legacy, payload, refs, missing in group:
            oid = observation_id(source, kind, legacy); old = lookup(state, source, kind, legacy)
            if missing:
                if oid in bindings:
                    raise ValueError('cannot bind a record with unresolved parent identities')
                proposals[legacy] = (None, 'unresolved_parent_identity', None)
            elif oid in bindings:
                proposals[legacy] = (_binding(state, oid, kind, key, bindings), 'explicit_identity_continuity', None)
            elif old and old['status'] == 'resolved':
                proposals[legacy] = (old['durable_id'], old['reason'], old)
            elif not candidates:
                target = _record(state, kind, key, fingerprint([source, kind, legacy]))
                proposals[legacy] = (target, 'first_observed_record_slot', None)
            else:
                aliases = {m['decisions'][-1]['durable_id'] for m in state['legacy_mappings'].values()
                           if m['kind'] == kind and m['legacy_id'] == legacy and
                           m['decisions'][-1]['durable_id'] in candidates}
                if len(aliases) == 1:
                    proposals[legacy] = (next(iter(aliases)), 'known_legacy_alias_and_durable_parents', None)
                elif kind == 'obligation':
                    # An expense can acquire another person's claim later. Cardinality alone
                    # cannot prove a renamed claimant is the same claim. An unchanged source
                    # label/direction within the same durable origin supports overlap imports;
                    # corrections need an explicit continuity decision, never person guessing.
                    hint = fingerprint(['claim_label', key, payload['direction'], payload['counterparty']])
                    supported = [i for i in candidates if payload['counterparty'] and hint in state['records'][i]['identity_keys']]
                    proposals[legacy] = ((supported[0], 'same_origin_and_source_claim_label', None) if len(supported) == 1 else
                                         (None, 'claim_continuity_requires_clarification', None))
                elif len(group) == len(candidates) == 1:
                    proposals[legacy] = (candidates[0], 'unique_durable_parent_slot', None)
                else:
                    proposals[legacy] = (None, 'record_slot_continuity_requires_clarification', None)
        used = Counter(target for target, _, _ in proposals.values() if target)
        for legacy, payload, refs, missing in group:
            target, reason, old = proposals[legacy]; oid = observation_id(source, kind, legacy)
            if target and used[target] > 1:
                target, reason, old = None, 'competing_records_for_identity', None
            if target and kind == 'obligation' and payload['counterparty']:
                hint = fingerprint(['claim_label', key, payload['direction'], payload['counterparty']])
                state['records'][target]['identity_keys'] = sorted(set(state['records'][target]['identity_keys'] + [hint]))
            if old:
                results[legacy] = target; continue
            results[legacy] = _observe(state, source, kind, legacy, payload, target, reason, refs,
                candidates=candidates if not target else (), unresolved=missing, continuity=bindings.get(oid))
    return results


def _references(specs):
    refs, missing = {}, {}
    for role, legacy_ids, mapping in specs:
        refs[role] = sorted({mapping[i] for i in legacy_ids if mapping.get(i)})
        absent = sorted({i for i in legacy_ids if not mapping.get(i)})
        if absent: missing[role] = absent
    return refs, missing


def migrate(snapshot, previous=None, *, metadata=None, continuity=None):
    """Add a coherent existing-pipeline snapshot to the sidecar; no financial files are written."""
    from .classification import build as classify
    from .obligations import build as originate
    from .reconcile import validate_result
    from .relationships import derive as relate
    from .settlements import build as settle
    from .output_models import (BankTransactions, EventDocument, ReconciledDocuments,
        RelationshipsOutput, ClassificationOutput, CoverageOutput, ObligationsOutput, SettlementsOutput)

    previous = empty_registry() if previous is None else previous
    validate_registry(previous)
    for rows in snapshot['banks'].values(): validate_schema(BankTransactions, rows, 'identity bank input')
    for document in snapshot['events'].values(): validate_schema(EventDocument, document, 'identity event input')
    for name, schema in [('reconciled', ReconciledDocuments), ('relationships', RelationshipsOutput),
                         ('classification', ClassificationOutput), ('coverage', CoverageOutput),
                         ('origins', ObligationsOutput), ('settlements', SettlementsOutput)]:
        validate_schema(schema, snapshot[name], 'identity ' + name + ' input')
    for document in snapshot['reconciled']:
        bank = document['bank'].lower()
        validate_result(document, snapshot['banks'][bank], snapshot['events'][bank])
    docs, rels, view, coverage, origins = (snapshot[k] for k in ('reconciled', 'relationships', 'classification', 'coverage', 'origins'))
    expected = [('relationships', relate(docs)), ('classification', classify(docs, rels)),
                ('origins', originate(docs, rels, view, coverage)),
                ('settlements', settle(docs, rels, view, coverage, origins))]
    for name, rebuilt in expected:
        if rebuilt != {k: v for k, v in snapshot[name].items() if k != 'verification'}:
            raise ValueError('identity snapshot differs from validated ' + name)
    state = deepcopy(previous); metadata = metadata or {}; bindings = continuity or {}
    sources = {}; transactions = {}; events = {}
    for bank, rows in sorted(snapshot['banks'].items()):
        role = 'bank:' + bank
        sources[role] = _source(state, 'bank', rows, metadata.get(role))
        mapped = _bank(state, rows, sources[role], bindings)
        if set(transactions) & set(mapped):
            raise ValueError('legacy transaction IDs collide within snapshot; import account snapshots separately')
        transactions.update(mapped)
    for bank, document in sorted(snapshot['events'].items()):
        role = 'events:' + bank; sources[role] = _source(state, 'events', document, metadata.get(role))
        reconciled = next(d for d in snapshot['reconciled'] if d['bank'].lower() == bank)
        attached = {r['event']['event_id']: r['bank_transaction']['transaction_id']
                    for r in reconciled['transactions'] if r['event']}
        items = []
        for event in document['events']:
            legacy = event['event_id']; tx = attached.get(legacy)
            refs, missing = _references([('transaction', [tx] if tx else [], transactions)])
            if not tx: missing['transaction'] = ['unmatched_event']
            items.append((legacy, event, refs['transaction'], refs, missing))
        events.update(_group(state, sources[role], 'event', items, bindings))

    for role in ('relationships', 'classification', 'coverage', 'origins', 'settlements'):
        sources[role] = _source(state, role, snapshot[role], metadata.get(role))

    items = []
    for r in snapshot['relationships']['relationships']:
        refs, missing = _references([('participants', r['transaction_ids'], transactions),
            ('candidates', r['candidate_transaction_ids'], transactions),
            ('events', [e['event_id'] for e in r['evidence'] if e['event_id']], events)])
        items.append((r['relationship_id'], r, refs['participants'], refs, missing))
    rels = _group(state, sources['relationships'], 'relationship', items, bindings)
    items = []
    for o in snapshot['origins']['obligations']:
        refs, missing = _references([('origins', o['originating_transaction_ids'], transactions),
            ('events', o['semantic_event_ids'], events), ('supporting_relationships', o['supporting_relationship_ids'], rels),
            ('context_relationships', [r['relationship_id'] for r in o['existing_relationship_context']], rels)])
        items.append((o['obligation_id'], o, refs['origins'], refs, missing))
    origins = _group(state, sources['origins'], 'obligation', items, bindings)
    items = []
    for q in snapshot['origins']['clarification_candidates']:
        refs, missing = _references([('transactions', q['transaction_ids'], transactions),
            ('events', q['event_ids'], events), ('obligations', q['obligation_ids'], origins),
            ('relationships', q['relationship_ids'], rels)])
        items.append((q['clarification_id'], q, refs['transactions'], refs, missing))
    questions = _group(state, sources['origins'], 'origin_question', items, bindings)
    items = []
    for a in snapshot['settlements']['allocations']:
        refs, missing = _references([('obligations', [a['obligation_id']], origins),
            ('repayments', [a['repayment_transaction_id']], transactions), ('relationships', a['relationship_ids'], rels)])
        items.append((a['allocation_id'], a, refs['obligations'] + refs['repayments'], refs, missing))
    allocations = _group(state, sources['settlements'], 'allocation', items, bindings)
    items = []
    for v in snapshot['settlements']['obligation_settlement_view']:
        refs, missing = _references([('obligations', [v['obligation_id']], origins),
            ('repayments', v['repayment_transaction_ids'], transactions), ('allocations', v['allocation_ids'], allocations),
            ('clarifications', v['existing_clarification_ids'], questions)])
        items.append((v['obligation_id'], v, refs['obligations'], refs, missing))
    _group(state, sources['settlements'], 'settlement', items, bindings)
    items = []
    for c in snapshot['settlements']['unresolved_candidates']:
        refs, missing = _references([('repayments', [c['repayment_transaction_id']], transactions),
            ('obligations', c['obligation_ids'], origins), ('relationships', c['relationship_ids'], rels)])
        items.append((c['candidate_id'], c, refs['repayments'], refs, missing))
    candidates = _group(state, sources['settlements'], 'candidate', items, bindings)
    items = []
    for q in snapshot['settlements']['clarification_candidates']:
        refs, missing = _references([('obligations', q['obligation_ids'], origins),
            ('repayments', [q['repayment_transaction_id']] if q['repayment_transaction_id'] else [], transactions),
            ('candidates', [q['candidate_id']] if q['candidate_id'] else [], candidates)])
        items.append((q['clarification_id'], q, refs['candidates'] or refs['obligations'], refs, missing))
    _group(state, sources['settlements'], 'settlement_question', items, bindings)

    # Repeated legacy transaction/event IDs in projections are aliases, not additional records.
    for document in snapshot['reconciled']:
        role = 'reconciled:' + document['bank'].lower()
        sid = _source(state, 'reconciled', document, metadata.get(role))
        for row in document['transactions']:
            bank = row['bank_transaction']['bank'].lower(); tx = row['bank_transaction']['transaction_id']
            original = lookup(state, sources['bank:' + bank], 'transaction', tx)
            _projection_alias(state, sid, 'transaction', tx, row, original)
            if row['event']:
                event = row['event']; original = lookup(state, sources['events:' + bank], 'event', event['event_id'])
                _projection_alias(state, sid, 'event', event['event_id'], event, original)
    for role in ('classification', 'coverage'):
        for row in snapshot[role]['transactions']:
            original = lookup(state, sources['bank:' + row['bank'].lower()], 'transaction', row['transaction_id'])
            _projection_alias(state, sources[role], 'transaction', row['transaction_id'], row, original)
    return _finish(state, bindings)


def _projection_alias(state, source, kind, legacy, payload, original):
    oid = observation_id(source, kind, legacy)
    mapping = dict(source_id=source, kind=kind, legacy_id=legacy, record_sha256=fingerprint(payload),
                   decisions=[deepcopy(original)])
    if oid not in state['legacy_mappings']:
        state['legacy_mappings'][oid] = mapping
    elif state['legacy_mappings'][oid]['decisions'][-1] != original:
        state['legacy_mappings'][oid]['decisions'].append(deepcopy(original))


def summary(state):
    decisions = [m['decisions'][-1] for m in state['legacy_mappings'].values()]
    return dict(records_by_kind=dict(sorted(Counter(r['kind'] for r in state['records'].values()).items())),
        sources=len(state['sources']), revisions=len(state['revisions']), legacy_mappings=len(decisions),
        unresolved_mappings=sum(d['status'] == 'unresolved' for d in decisions))


def run(root=ROOT):
    """Migrate the existing local pipeline snapshot, writing only the identity sidecar."""
    root = Path(root)
    destination = root/'output/identities/identity_registry.json'
    protected = {p: p.read_bytes() for folder in ('input', 'output') for p in (root/folder).rglob('*')
                 if p.is_file() and not p.is_relative_to(root/'output/identities')}
    metadata = {}
    def read(name, role, upstream=None):
        path = root/name; raw = protected[path]
        metadata[role] = dict(location=name, content_sha256=hashlib.sha256(raw).hexdigest(),
            upstream_files={p: hashlib.sha256(protected[root/p]).hexdigest() for p in upstream or []})
        return json.loads(raw)
    coverage = read('output/audit/coverage_audit.json', 'coverage')
    banks, events, documents = {}, {}, []
    for bank in ('kotak', 'sbi'):
        inventory = coverage['sources'][bank]
        banks[bank] = read(f'output/bank/{bank}_transactions.json', 'bank:' + bank, [inventory['bank_file']])
        events[bank] = read(f'output/events/{bank}_events.json', 'events:' + bank, [inventory['notes_file']])
        documents.append(read(f'output/reconciled/{bank}_reconciled.json', 'reconciled:' + bank))
    snapshot = dict(banks=banks, events=events, reconciled=documents, coverage=coverage,
        relationships=read('output/relationships/relationships.json', 'relationships'),
        classification=read('output/classification/effective_transactions.json', 'classification'),
        origins=read('output/obligations/obligations.json', 'origins'),
        settlements=read('output/settlements/settlements.json', 'settlements'))
    previous_bytes = destination.read_bytes() if destination.exists() else None
    previous = parse_document(previous_bytes, Registry, str(destination)) if previous_bytes is not None else empty_registry()
    result = migrate(snapshot, previous, metadata=metadata)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    validate_registry(json.loads(encoded))
    if any(p.read_bytes() != content for p, content in protected.items()):
        raise ValueError('source changed during identity migration')
    if (destination.read_bytes() if destination.exists() else None) != previous_bytes:
        raise ValueError('identity registry changed during migration')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.tmp')
    try:
        temporary.write_text(encoded, encoding='utf-8'); temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return result
