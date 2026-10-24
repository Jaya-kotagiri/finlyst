"""3C.1: one effective treatment per bank movement, without settlement accounting."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

from .reconcile import digest
from .relationships import flatten, validate_relationships

ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_FIELDS = ('event_id', 'source_id', 'event_type', 'merchant', 'payee', 'payer',
                   'purpose_context', 'category', 'subcategory', 'description', 'confidence', 'ambiguities')
EXPENSE_CATEGORIES = {
    'food', 'groceries', 'transport', 'transportation', 'travel', 'health', 'healthcare',
    'office supplies', 'subscription', 'utilities', 'household services', 'services',
    'bank fees', 'gifts', 'personal expense', 'event expense',
}
EXPENSE_TYPES = {
    'purchase', 'software purchase', 'subscription payment', 'medical expense', 'transport expense',
    'food expense', 'grocery purchase', 'utility payment', 'service payment',
    'mobile recharge', 'payment for venue', 'gift expense', 'bank fee',
    'shared food expense', 'shared transport expense',
}
SPECIAL_SUPPORT = {
    'self_transfer': ('self_transfer', {'confirmed'}),
    'cash_conversion': ('cash_conversion', {'confirmed', 'source_asserted'}),
    'loan_advance': ('loan_advance', {'identified'}),
    'loan_borrowing': ('loan_borrowing', {'identified'}),
    'recoverable_or_shared_expense': ('reimbursement_expected', {'identified'}),
}


def classify_record(record, treatment, related):
    bank, event = record['bank'], record['event']
    active = [r for r in related if bank['transaction_id'] in r['transaction_ids']]
    special = treatment['classification']
    result = dict(treatment='unknown', income=None, expense=None, status='unknown',
                  source='insufficient_evidence', rule='no_supported_financial_treatment',
                  confidence='low', supporting_relationship_ids=[])
    if special != 'unclassified':
        support = []
        if special in SPECIAL_SUPPORT:
            kind, statuses = SPECIAL_SUPPORT[special]
            support = [r for r in active if r['type'] == kind and r['status'] in statuses]
        elif special == 'reimbursement_received':
            support = [r for r in active if r['type'] == 'reimbursement' and
                       (r['status'] == 'confirmed' or r['details'].get('receipt_classification_supported') is True)]
        elif special == 'salary_income':
            if bank['direction'] != 'credit' or not re.search(r'my salary.*(?:credit|received)', record['note']):
                raise ValueError('salary treatment lacks source support')
        else:
            raise ValueError('unsupported special financial treatment')
        if special != 'salary_income' and not support:
            raise ValueError('special treatment lacks applicable relationship evidence')
        expected_direction = {'cash_conversion': 'debit', 'loan_advance': 'debit',
            'loan_borrowing': 'credit', 'recoverable_or_shared_expense': 'debit',
            'reimbursement_received': 'credit', 'salary_income': 'credit'}.get(special)
        if expected_direction and bank['direction'] != expected_direction:
            raise ValueError('financial treatment conflicts with bank direction')
        result.update(treatment=special, income=treatment['income'], expense=treatment['expense'],
            status='allocation_deferred' if special == 'recoverable_or_shared_expense' else 'supported',
            source='financial_relationships', rule='supported_3B_treatment_precedes_semantic_type',
            confidence='medium' if any(r['status'] in ('source_asserted', 'unresolved') for r in support) else 'high',
            supporting_relationship_ids=sorted(r['relationship_id'] for r in support))
        return result
    # A potential relationship does not become an executed financial treatment.
    # But uncertainty about who bears a payment blocks an automatic expense assumption.
    if any(r['type'] in ('payment_on_behalf', 'purpose_specific_funding') for r in active):
        result['rule'] = 'funding_or_beneficiary_terms_unresolved'
        return result
    narration = bank['raw_description']
    explicit_fee = bool(re.search(r'CHRG:SMS ALERT FEE|ATMCard\s+AMC\b', narration, re.I))
    if bank['direction'] == 'debit' and explicit_fee:
        result.update(treatment='expense', income=False, expense=True, status='supported',
                      source='bank_facts', rule='explicit_bank_fee_narration', confidence='high')
        return result
    if (bank['direction'] == 'debit' and event and record['note'] and
        event['confidence'] != 'low' and not event['ambiguities'] and
        (event['event_type'] in EXPENSE_TYPES or event['category'] in EXPENSE_CATEGORIES)):
        # A transfer/loan claim without supporting 3B evidence is not a normal purchase.
        if any(term in (event['event_type'] or '').lower() for term in ('loan', 'transfer', 'cash')):
            result['rule'] = 'special_semantic_claim_without_financial_support'
            return result
        result.update(treatment='expense', income=False, expense=True, status='supported',
                      source='matched_semantics', rule='noted_ordinary_purchase_or_service',
                      confidence=event['confidence'])
    return result


def build(documents, relationships):
    records = flatten(documents)  # Reuses complete 3A source and result validation.
    expected_fingerprints = {d['bank']: digest(d) for d in documents}
    if relationships['source_fingerprints'] != expected_fingerprints:
        raise ValueError('stale or unrelated 3B input fingerprints')
    validate_relationships(relationships, records)
    treatments = {t['transaction_id']: t for t in relationships['transaction_treatments']}
    rows = []
    for identifier in sorted(records):
        record = records[identifier]; event = record['event']
        related = sorted([r for r in relationships['relationships'] if identifier in
                          r['transaction_ids'] + r['candidate_transaction_ids']], key=lambda r: r['relationship_id'])
        effective = classify_record(record, treatments[identifier], related)
        review = []
        if effective['status'] == 'unknown': review.append('financial_treatment_unknown')
        if effective['status'] == 'allocation_deferred': review.append('personal_expense_allocation_deferred')
        if event and (event['confidence'] == 'low' or event['ambiguities']): review.append('semantic_uncertainty')
        if any(r['status'] == 'unresolved' for r in related): review.append('relationship_unresolved')
        rows.append(dict(deepcopy(record['bank']),
            semantic={field: deepcopy(event[field]) for field in SEMANTIC_FIELDS} if event else None,
            effective=effective,
            relationships=[{'relationship_id': r['relationship_id'], 'type': r['type'], 'status': r['status'],
                            'role': 'participant' if identifier in r['transaction_ids'] else 'candidate'} for r in related],
            review_reasons=review,
            provenance={'bank_transaction_id': identifier,
                        'semantic_event_id': event['event_id'] if event else None,
                        'semantic_event_sha256': digest(event) if event else None,
                        'source_row_id': record['source'].get('source_id'),
                        'source_row_sha256': digest(record['source']) if record['source'] else None,
                        'original_3B_classification': treatments[identifier]['classification']}))
    event_ids = [r['semantic']['event_id'] for r in rows if r['semantic']]
    result = {'schema_version': '3C.1.1',
        'input_fingerprints': {'reconciled': dict(sorted(expected_fingerprints.items())), 'relationships': digest(relationships)},
        'transactions': rows,
        'report': {'transactions': len(rows), 'semantic_events': len(event_ids),
                   'without_semantic_event': len(rows) - len(event_ids),
                   'by_effective_treatment': dict(sorted(Counter(r['effective']['treatment'] for r in rows).items())),
                   'by_classification_source': dict(sorted(Counter(r['effective']['source'] for r in rows).items())),
                   'unknown_treatment': sum(r['effective']['status'] == 'unknown' for r in rows),
                   'allocation_deferred': sum(r['effective']['status'] == 'allocation_deferred' for r in rows),
                   'with_unresolved_relationships': sum('relationship_unresolved' in r['review_reasons'] for r in rows),
                   'with_semantic_uncertainty': sum('semantic_uncertainty' in r['review_reasons'] for r in rows)},
        'counting_policy': 'One effective treatment per bank transaction. Never count relationship records as flows. Null flags mean unknown/deferred, not zero. Semantic category does not override effective treatment.'}
    validate_view(result, records)
    return result


def validate_view(result, records):
    rows = result['transactions']
    if len(rows) != len(records) or {r['transaction_id'] for r in rows} != set(records):
        raise ValueError('transaction coverage changed')
    seen = []
    for row in rows:
        original = records[row['transaction_id']]
        if {k: row[k] for k in original['bank']} != original['bank']:
            raise ValueError('authoritative bank facts changed')
        event = original['event']
        expected = {field: event[field] for field in SEMANTIC_FIELDS} if event else None
        if row['semantic'] != expected:
            raise ValueError('semantic meaning changed or lost')
        if event: seen.append(event['event_id'])
        e = row['effective']
        flags = (e['income'], e['expense'])
        if e['treatment'] == 'expense' and (flags != (False, True) or row['direction'] != 'debit'):
            raise ValueError('invalid expense treatment')
        if e['treatment'] == 'salary_income' and (flags != (True, False) or row['direction'] != 'credit'):
            raise ValueError('invalid income treatment')
        if e['treatment'] in ('self_transfer', 'cash_conversion', 'loan_advance', 'loan_borrowing', 'reimbursement_received') and flags != (False, False):
            raise ValueError('non-income/expense movement double counted')
        if e['treatment'] == 'unknown' and flags != (None, None):
            raise ValueError('unknown treatment forced into totals')
        if e['treatment'] == 'recoverable_or_shared_expense' and flags != (False, None):
            raise ValueError('shared payment incorrectly counted as personal expense')
    expected_events = [r['event']['event_id'] for r in records.values() if r['event']]
    if len(seen) != len(set(seen)) or set(seen) != set(expected_events):
        raise ValueError('semantic event coverage not one-to-one')


def run(reconciled_paths, relationship_path, output):
    paths = list(map(Path, reconciled_paths)); relationship_path = Path(relationship_path)
    snapshots = {p: p.read_bytes() for p in paths + [relationship_path]}
    documents = [json.loads(snapshots[p]) for p in paths]
    relationships = json.loads(snapshots[relationship_path])
    result = build(documents, relationships)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    validate_view(json.loads(encoded), flatten(documents))
    output = Path(output)
    if output.resolve() in {p.resolve() for p in snapshots}:
        raise ValueError('output cannot overwrite source')
    if any(p.read_bytes() != content for p, content in snapshots.items()):
        raise ValueError('input changed during classification')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.json.tmp')
    try:
        temporary.write_text(encoded, encoding='utf-8'); temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return result['report']


if __name__ == '__main__':
    print(json.dumps(run([ROOT / f'output/reconciled/{b}_reconciled.json' for b in ('kotak', 'sbi')],
        ROOT / 'output/relationships/relationships.json', ROOT / 'output/classification/effective_transactions.json'), indent=2))
