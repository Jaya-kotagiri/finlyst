"""Pure projections from validated snapshots and existing durable mappings to SQL rows.

Source-specific IDs, hashes and complete original evidence belong to source observations.
Revision-keyed JSON follows the registry's semantic fingerprint contract so renumbered
overlapping sources cannot silently replace an existing interpretation revision.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import re
from uuid import UUID

from .identities import _semantic, fingerprint, lookup, observation_id


TABLES = ('accounts', 'bank_transactions', 'semantic_events', 'relationships',
          'relationship_transactions', 'effective_classifications', 'classification_relationships',
          'obligations', 'obligation_links', 'settlements', 'allocations', 'settlement_allocations')


def _money(value):
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r'-?\d+\.\d{2}', value) is None:
        raise ValueError('Database projection requires normalized monetary strings')
    return Decimal(value)


def _day(value):
    return date.fromisoformat(value) if value is not None else None


def project(snapshot, registry, source_ids):
    """Return typed row dictionaries without I/O, inference, mutation or new identities."""
    rows = {table: [] for table in TABLES}
    seen = {table: {} for table in TABLES}

    def append(table, row, key=None):
        key = key if key is not None else row['revision_id']
        previous = seen[table].get(key)
        if previous is not None:
            if previous != row:
                raise ValueError(f'{table}: one identity/revision has incompatible projected values')
            return
        seen[table][key] = row
        rows[table].append(row)

    def resolve(role, kind, legacy, payload=None):
        source = source_ids[role]
        decision = lookup(registry, source, kind, legacy)
        if decision is None or decision['status'] != 'resolved':
            raise ValueError(f'{role}: {kind} {legacy} has no resolved durable identity')
        if payload is not None:
            mapping = registry['legacy_mappings'][observation_id(source, kind, legacy)]
            if mapping['record_sha256'] != fingerprint(payload):
                raise ValueError(f'{role}: {kind} {legacy} source payload differs from its identity mapping')
        record, revision = decision['durable_id'], decision['revision_id']
        if registry['records'][record]['kind'] != kind or registry['revisions'][revision]['record_id'] != record:
            raise ValueError(f'{role}: {kind} {legacy} has an inconsistent revision identity')
        return dict(record_id=UUID(record), revision_id=UUID(revision), record_kind=kind)

    def register(mapping, legacy, value, kind):
        if legacy in mapping:
            raise ValueError(f'Duplicate active {kind} legacy identifier in snapshot')
        if value['record_id'] in {item['record_id'] for item in mapping.values()}:
            raise ValueError(f'Multiple active {kind} rows resolve to one durable record')
        mapping[legacy] = value

    def record(mapping, legacy):
        if legacy not in mapping:
            raise ValueError('Database projection references a record outside the validated snapshot')
        return mapping[legacy]['record_id']

    transactions, events, relationships, obligations, allocations = {}, {}, {}, {}, {}
    for bank, bank_rows in sorted(snapshot['banks'].items()):
        role = 'bank:' + bank
        for transaction in bank_rows:
            account = transaction['account']
            account_identity = None
            if account is not None:
                account_identity = resolve(role, 'account', transaction['bank'] + ':' + account,
                                           {'bank': transaction['bank'], 'account': account})
                append('accounts', dict(account_identity, bank=transaction['bank'], account_number=account))
            identity = resolve(role, 'transaction', transaction['transaction_id'], transaction)
            register(transactions, transaction['transaction_id'], identity, 'transaction')
            append('bank_transactions', dict(identity,
                account_id=account_identity['record_id'] if account_identity else None, account_kind='account',
                bank=transaction['bank'], transaction_date=_day(transaction['transaction_date']),
                amount=_money(transaction['amount']), direction=transaction['direction'],
                raw_description=transaction['raw_description'], reference=transaction['reference'],
                balance_after=_money(transaction['balance_after'])))

    event_transactions = {row['event']['event_id']: row['bank_transaction']['transaction_id']
        for document in snapshot['reconciled'] for row in document['transactions'] if row['event']}
    scalar_event_fields = ('event_type', 'direction', 'currency', 'merchant', 'payee', 'payer', 'category',
                           'subcategory', 'description', 'purpose_context', 'confidence')
    context_fields = ('entities', 'components', 'matching_clues', 'ambiguities', 'additional_context')
    for bank, document in sorted(snapshot['events'].items()):
        for event in document['events']:
            identity = resolve('events:' + bank, 'event', event['event_id'], event)
            register(events, event['event_id'], identity, 'event')
            transaction = event_transactions.get(event['event_id'])
            if transaction is None:
                raise ValueError('A resolved event has no reconciled bank transaction in the snapshot')
            append('semantic_events', dict(identity, transaction_id=record(transactions, transaction),
                transaction_kind='transaction', event_date=_day(event['date']), amount=_money(event['amount']),
                context=_semantic({key: event[key] for key in context_fields}),
                **{key: event[key] for key in scalar_event_fields}))

    for relationship in snapshot['relationships']['relationships']:
        identity = resolve('relationships', 'relationship', relationship['relationship_id'], relationship)
        register(relationships, relationship['relationship_id'], identity, 'relationship')
        append('relationships', dict(identity, relationship_type=relationship['type'], status=relationship['status'],
            rule=relationship['rule'], confidence=relationship['confidence'], purpose=relationship['purpose'],
            financial_treatment=_semantic(relationship['financial_treatment']),
            evidence=_semantic(relationship['evidence']), details=_semantic(relationship['details'])))
        for role, field in (('participant', 'transaction_ids'), ('candidate', 'candidate_transaction_ids')):
            for identifier in relationship[field]:
                transaction = record(transactions, identifier)
                append('relationship_transactions', dict(relationship_revision_id=identity['revision_id'],
                    transaction_id=transaction, transaction_kind='transaction', role=role),
                    (identity['revision_id'], transaction, role))

    classification_source = UUID(source_ids['classification'])
    for item in snapshot['classification']['transactions']:
        # This projection is source-scoped, so retain its complete original provenance.
        resolve('classification', 'transaction', item['transaction_id'], item)
        transaction = record(transactions, item['transaction_id'])
        semantic, effective = item['semantic'], item['effective']
        append('effective_classifications', dict(source_document_id=classification_source,
            transaction_id=transaction, transaction_kind='transaction',
            event_id=record(events, semantic['event_id']) if semantic else None, event_kind='event',
            treatment=effective['treatment'], income=effective['income'], expense=effective['expense'],
            status=effective['status'], classification_source=effective['source'], rule=effective['rule'],
            confidence=effective['confidence'], semantic=deepcopy(semantic), evidence=deepcopy({
                'provenance': item['provenance'], 'review_reasons': item['review_reasons'],
                'relationships': item['relationships'],
                'supporting_relationship_ids': effective['supporting_relationship_ids']})),
            (classification_source, transaction))
        for relationship in item['relationships']:
            target = record(relationships, relationship['relationship_id'])
            role = relationship['role']
            append('classification_relationships', dict(source_document_id=classification_source,
                transaction_id=transaction, relationship_id=target, relationship_kind='relationship', role=role),
                (classification_source, transaction, target, role))

    for obligation in snapshot['origins']['obligations']:
        identity = resolve('origins', 'obligation', obligation['obligation_id'], obligation)
        register(obligations, obligation['obligation_id'], identity, 'obligation')
        append('obligations', dict(identity, obligation_type=obligation['type'], direction=obligation['direction'],
            counterparty=obligation['counterparty'], purpose=obligation['purpose'],
            original_amount=_money(obligation['original_amount']), currency=obligation['currency'],
            currency_basis=obligation['currency_basis'], origin_date=_day(obligation['origin_date']),
            origin_status=obligation['origin_status'], settlement_status=obligation['settlement_status'],
            requires_clarification=obligation['requires_clarification'], confidence=obligation['confidence'],
            # Source provenance is deliberately excluded from registry revision identity.
            # Keep it verbatim in source_observations, not a first-seen revision payload.
            evidence=_semantic(obligation['evidence']), provenance={}))
        links = [('origin', obligation['originating_transaction_ids'], transactions, 'transaction'),
                 ('event', obligation['semantic_event_ids'], events, 'event'),
                 ('supporting_relationship', obligation['supporting_relationship_ids'], relationships, 'relationship'),
                 ('context_relationship', [r['relationship_id'] for r in obligation['existing_relationship_context']],
                  relationships, 'relationship')]
        for role, identifiers, mapping, kind in links:
            for identifier in identifiers:
                target = record(mapping, identifier)
                append('obligation_links', dict(obligation_revision_id=identity['revision_id'], role=role,
                    target_record_id=target, target_kind=kind), (identity['revision_id'], role, target))

    for allocation in snapshot['settlements']['allocations']:
        identity = resolve('settlements', 'allocation', allocation['allocation_id'], allocation)
        register(allocations, allocation['allocation_id'], identity, 'allocation')
        append('allocations', dict(identity, obligation_id=record(obligations, allocation['obligation_id']),
            obligation_kind='obligation', repayment_transaction_id=record(transactions, allocation['repayment_transaction_id']),
            transaction_kind='transaction', amount=_money(allocation['amount']), currency=allocation['currency'],
            rule=allocation['rule'], explicit_split=allocation['explicit_split'],
            full_settlement_intent=allocation['full_settlement_intent'], explicit_minor_overpayment=allocation['explicit_minor_overpayment'],
            confidence=allocation['confidence'], evidence_strength=allocation['evidence_strength'],
            confirmation_method=allocation['confirmation_method'], evidence=_semantic(allocation['evidence'])))

    for settlement in snapshot['settlements']['obligation_settlement_view']:
        identity = resolve('settlements', 'settlement', settlement['obligation_id'], settlement)
        obligation = record(obligations, settlement['obligation_id'])
        append('settlements', dict(identity, obligation_id=obligation, obligation_kind='obligation',
            direction=settlement['direction'], currency=settlement['currency'],
            original_principal=_money(settlement['original_principal']), verified_repayments=_money(settlement['verified_repayments']),
            verified_zero_repayments=settlement['verified_zero_repayments'], adjustment=_money(settlement['adjustment']),
            unadjusted_residual=_money(settlement['unadjusted_residual']), outstanding_amount=_money(settlement['outstanding_amount']),
            status=settlement['status'], as_of=_day(settlement['as_of']), reason=settlement['reason'],
            evidence_strength=settlement['repayment_evidence_strength'], confirmation_methods=list(settlement['confirmation_methods']),
            evidence=_semantic({'adjustment_evidence': settlement['adjustment_evidence'],
                                'balance_assertion': settlement['balance_assertion']})))
        for identifier in settlement['allocation_ids']:
            allocation = allocations[identifier]
            append('settlement_allocations', dict(settlement_revision_id=identity['revision_id'],
                allocation_revision_id=allocation['revision_id'], allocation_record_id=allocation['record_id'],
                obligation_id=obligation), (identity['revision_id'], allocation['record_id']))
    return rows
