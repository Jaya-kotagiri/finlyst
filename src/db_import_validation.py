"""Check current source observations against saved identities without creating IDs.

Registry self-consistency alone cannot prove that a previously resolved mapping
still describes its source payload. These checks bind interpretation hashes and
durable references to the exact current documents before database projection.
"""

from .identities import _semantic, fingerprint, observation_id


def validate_current_mappings(snapshot, registry, source_ids):
    """Reject stale/corrupted current mappings; retain historical mappings untouched."""
    observations = {}
    primary = {}

    def add(role, kind, legacy, payload, references=None, *, alias=None):
        oid = observation_id(source_ids[role], kind, legacy)
        if oid in observations:
            if kind == 'account' and observations[oid]['payload'] == payload:
                return
            raise ValueError('Database import: duplicate current source observation')
        mapping = registry['legacy_mappings'].get(oid)
        if mapping is None or mapping['record_sha256'] != fingerprint(payload):
            raise ValueError(f'Database import: {role} {kind} observation hash/mapping mismatch')
        decision = mapping['decisions'][-1]
        if decision['status'] != 'resolved':
            raise ValueError(f'Database import: {role} {kind} identity requires resolution')
        revision = registry['revisions'].get(decision['revision_id'])
        record = registry['records'].get(decision['durable_id'])
        if (revision is None or record is None or record['kind'] != kind
                or revision['record_id'] != decision['durable_id']):
            raise ValueError(f'Database import: {role} {kind} revision ownership mismatch')
        observations[oid] = dict(role=role, kind=kind, payload=payload, references=references or {},
                                 decision=decision, revision=revision, alias=alias)
        if alias is None:
            key = (kind, legacy)
            if key in primary:
                raise ValueError('Database import: duplicate current primary legacy identity')
            primary[key] = decision

    for bank, rows in sorted(snapshot['banks'].items()):
        role = 'bank:' + bank
        for row in rows:
            references = {}
            if row['account'] is not None:
                account = row['bank'] + ':' + row['account']
                add(role, 'account', account, {'bank': row['bank'], 'account': row['account']})
                references['account'] = [('account', account)]
            add(role, 'transaction', row['transaction_id'], row, references)

    linked_events = {}
    for document in snapshot['reconciled']:
        for row in document['transactions']:
            if row['event']:
                event = row['event']['event_id']
                if event in linked_events:
                    raise ValueError('Database import: semantic event linked more than once')
                linked_events[event] = row['bank_transaction']['transaction_id']

    for bank, document in sorted(snapshot['events'].items()):
        for event in document['events']:
            legacy = event['event_id']
            if legacy not in linked_events:
                raise ValueError('Database import: semantic event has no reconciled bank transaction')
            add('events:' + bank, 'event', legacy, event,
                {'transaction': [('transaction', linked_events[legacy])]})

    def refs(kind, identifiers):
        return [(kind, identifier) for identifier in identifiers]

    for row in snapshot['relationships']['relationships']:
        add('relationships', 'relationship', row['relationship_id'], row, {
            'participants': refs('transaction', row['transaction_ids']),
            'candidates': refs('transaction', row['candidate_transaction_ids']),
            'events': refs('event', [e['event_id'] for e in row['evidence'] if e['event_id']])})
    for row in snapshot['origins']['obligations']:
        add('origins', 'obligation', row['obligation_id'], row, {
            'origins': refs('transaction', row['originating_transaction_ids']),
            'events': refs('event', row['semantic_event_ids']),
            'supporting_relationships': refs('relationship', row['supporting_relationship_ids']),
            'context_relationships': refs('relationship', [r['relationship_id']
                                                         for r in row['existing_relationship_context']])})
    for row in snapshot['origins']['clarification_candidates']:
        add('origins', 'origin_question', row['clarification_id'], row, {
            'transactions': refs('transaction', row['transaction_ids']),
            'events': refs('event', row['event_ids']),
            'obligations': refs('obligation', row['obligation_ids']),
            'relationships': refs('relationship', row['relationship_ids'])})
    for row in snapshot['settlements']['allocations']:
        add('settlements', 'allocation', row['allocation_id'], row, {
            'obligations': refs('obligation', [row['obligation_id']]),
            'repayments': refs('transaction', [row['repayment_transaction_id']]),
            'relationships': refs('relationship', row['relationship_ids'])})
    for row in snapshot['settlements']['obligation_settlement_view']:
        add('settlements', 'settlement', row['obligation_id'], row, {
            'obligations': refs('obligation', [row['obligation_id']]),
            'repayments': refs('transaction', row['repayment_transaction_ids']),
            'allocations': refs('allocation', row['allocation_ids']),
            'clarifications': refs('origin_question', row['existing_clarification_ids'])})
    for row in snapshot['settlements']['unresolved_candidates']:
        add('settlements', 'candidate', row['candidate_id'], row, {
            'repayments': refs('transaction', [row['repayment_transaction_id']]),
            'obligations': refs('obligation', row['obligation_ids']),
            'relationships': refs('relationship', row['relationship_ids'])})
    for row in snapshot['settlements']['clarification_candidates']:
        add('settlements', 'settlement_question', row['clarification_id'], row, {
            'obligations': refs('obligation', row['obligation_ids']),
            'repayments': refs('transaction', [row['repayment_transaction_id']]
                              if row['repayment_transaction_id'] else []),
            'candidates': refs('candidate', [row['candidate_id']] if row['candidate_id'] else [])})

    for document in snapshot['reconciled']:
        role = 'reconciled:' + document['bank'].lower()
        for row in document['transactions']:
            legacy = row['bank_transaction']['transaction_id']
            add(role, 'transaction', legacy, row, alias=('transaction', legacy))
            if row['event']:
                event = row['event']
                add(role, 'event', event['event_id'], event, alias=('event', event['event_id']))
    for role in ('classification', 'coverage'):
        for row in snapshot[role]['transactions']:
            add(role, 'transaction', row['transaction_id'], row, alias=('transaction', row['transaction_id']))

    for observation in observations.values():
        decision, revision = observation['decision'], observation['revision']
        if observation['alias'] is not None:
            original = primary.get(observation['alias'])
            if original is None or decision != original:
                raise ValueError('Database import: projection alias differs from primary source identity')
            continue
        payload = observation['payload']
        interpretation = ({k: v for k, v in payload.items() if k != 'transaction_id'}
                          if observation['kind'] == 'transaction' else _semantic(payload))
        if revision['interpretation_sha256'] != fingerprint(interpretation):
            raise ValueError(f'Database import: {observation["kind"]} interpretation hash differs from source')
        expected = {}
        for role, references in observation['references'].items():
            if any(reference not in primary for reference in references):
                raise ValueError('Database import: financial reference lies outside current snapshot')
            expected[role] = sorted({primary[reference]['durable_id'] for reference in references})
        if revision['references'] != expected or decision['references'] != expected:
            raise ValueError(f'Database import: {observation["kind"]} durable references differ from source')

    current_sources = set(source_ids.values())
    actual = {oid for oid, mapping in registry['legacy_mappings'].items()
              if mapping['source_id'] in current_sources}
    if actual != observations.keys():
        raise ValueError('Database import: current registry source observation coverage differs from documents')
