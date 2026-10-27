"""Phase 3B: evidence-backed financial relationships; no settlement ledger or API."""

from collections import Counter
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .reconcile import digest, validate_result
from .output_models import ReconciledDocuments, ReconciliationOutput, RelationshipsOutput
from .schema_validation import parse_document, validate_schema

ROOT = Path(__file__).resolve().parents[1]


def text(value):
    return ' '.join((value or '').lower().split())


def upi_reference(narration):
    compact = ''.join(narration.split())
    match = re.search(r'UPI/(?:DR|CR)/(\d{12})/', compact, re.I)
    if match:
        return match[1]
    # Kotak puts its interbank UPI ID after the counterparty and bank code.
    match = re.search(r'UPI/[^/]+/[^/]+/(\d{12})/', compact, re.I)
    return match[1] if match else None


def counterparty_key(narration):
    compact = ''.join(narration.split()).lower()
    match = re.search(r'upi/(?:dr|cr)/\d{12}/([^/]+)/([^/]+)/([^/]+)/', compact)
    return tuple(match.groups()) if match else None


def after(origin, other, max_days=30):
    delta = (date.fromisoformat(other['transaction_date']) - date.fromisoformat(origin['transaction_date'])).days
    if not 0 <= delta <= max_days:
        return False
    if delta == 0 and origin['bank'] == other['bank']:
        return origin['transaction_id'] < other['transaction_id']
    return True  # Intraday cross-bank ordering is unavailable; no precise timestamp claim.


def repayment_note(note):
    return bool(re.search(r'(?:sent|paid|repaid|returned|reimbursed).*(?:share|split|prev.*payment|ticket.*booked|reimburse)', note))


def mentions_party(party, note):
    return bool(party and re.search(r'(?<!\w)' + re.escape(party) + r"(?:s|'s)?(?!\w)", note))


def flatten(documents):
    validate_schema(ReconciledDocuments, documents, "reconciled inputs")
    records = {}
    for document in sorted(documents, key=lambda d: d['bank']):
        bank = [t['bank_transaction'] for t in document['transactions']]
        validate_result(document, bank, document['event_source_document'])
        sources = {r['source_id']: r for r in document['event_source_document']['source_rows']}
        for item in document['transactions']:
            transaction = item['bank_transaction']
            identifier = transaction['transaction_id']
            if identifier in records:
                raise ValueError('duplicate transaction IDs across documents')
            event = item['event']
            source = sources[event['source_id']] if event else {}
            records[identifier] = {'bank': transaction, 'event': event, 'source': source,
                'note': text(source.get('note')), 'follow_up': text(source.get('follow_up'))}
    return records


def derive(documents):
    records = flatten(documents)
    relationships = []
    treatments = {identifier: {'transaction_id': identifier, 'classification': 'unclassified',
        'income': None, 'expense': None, 'relationship_ids': [], 'reason': 'Insufficient supported classification.'}
        for identifier in records}

    def evidence(ids):
        return [{'transaction_id': identifier,
                 'event_id': records[identifier]['event']['event_id'] if records[identifier]['event'] else None,
                 'source_id': records[identifier]['source'].get('source_id'),
                 'note': records[identifier]['source'].get('note'),
                 'follow_up': records[identifier]['source'].get('follow_up')}
                for identifier in ids]

    def add(kind, ids, status, rule, purpose=None, candidate_ids=None, details=None):
        ids = list(ids)
        identity = {'type': kind, 'transaction_ids': ids, 'status': status, 'rule': rule}
        relationship = dict(relationship_id='REL_' + digest(identity)[:16], **identity,
            confidence='high' if status == 'confirmed' else 'medium' if status in ('source_asserted', 'planned', 'identified') else 'low',
            purpose=purpose, candidate_transaction_ids=sorted(candidate_ids or []),
            evidence=evidence(ids), details=details or {}, financial_treatment={
                'no_independent_cashflow': True,
                'instruction': 'Use transaction_treatments once per transaction; never sum relationship records as extra flows.'})
        relationships.append(relationship)
        for identifier in ids:
            treatments[identifier]['relationship_ids'].append(relationship['relationship_id'])
        return relationship

    def classify(identifier, classification, income, expense, reason):
        previous = treatments[identifier]
        if previous['classification'] not in ('unclassified', classification):
            raise ValueError('conflicting financial treatments: ' + identifier)
        previous.update(classification=classification, income=income, expense=expense, reason=reason)

    # The reconciled files represent owned accounts. A common interbank UPI ID is
    # independent bank evidence; matching amounts or counterparty names alone are insufficient.
    transfer_pairs = []
    for di, debit in records.items():
        a = debit['bank']; token = upi_reference(a['raw_description'])
        if a['direction'] != 'debit' or not token:
            continue
        for ci, credit in records.items():
            b = credit['bank']
            if (b['direction'] == 'credit' and a['bank'] != b['bank'] and a['amount'] == b['amount']
                and a['transaction_date'] == b['transaction_date'] and token == upi_reference(b['raw_description'])):
                transfer_pairs.append((di, ci, token))
    owners = Counter(i for pair in transfer_pairs for i in pair[:2])
    unresolved_transfers = set()
    for di, ci, token in transfer_pairs:
        if owners[di] == owners[ci] == 1:
            add('self_transfer', [di, ci], 'confirmed', 'same_upi_id_date_amount_opposite_owned_accounts',
                purpose=records[di]['source'].get('note') or records[ci]['source'].get('note'),
                details={'shared_upi_id': token, 'execution': 'bank_confirmed'})
            for identifier in (di, ci):
                classify(identifier, 'self_transfer', False, False, 'Owned-account movement, not income or expense.')
        else:
            if di not in unresolved_transfers:
                add('self_transfer', [di], 'unresolved', 'duplicate_transfer_candidates',
                    candidate_ids=[c for d, c, _ in transfer_pairs if d == di])
                unresolved_transfers.add(di)

    for identifier, record in records.items():
        bank, event, note = record['bank'], record['event'], record['note']
        follow = record['follow_up']
        if treatments[identifier]['classification'] == 'self_transfer':
            continue
        direction = bank['direction']
        # Planned instructions are not evidence of an additional completed bank movement.
        if re.search(r'(?:need to send|supposed to send|send this to|should.*(?:send|move)|must.*(?:send|move))', note + ' ' + follow):
            add('planned_transfer', [identifier], 'planned', 'explicit_future_transfer_instruction',
                purpose=record['source'].get('follow_up') or record['source'].get('note'),
                details={'execution': 'not_established', 'creates_cashflow': False})
        cash_note = re.search(r'gave me.*\bin cash\b', note) and re.search(r'\bupi\b', note)
        atm = bool(re.match(r'\s*ATM WDL\b', bank['raw_description'], re.I))
        if direction == 'debit' and (atm or cash_note):
            if cash_note and not any(Decimal(n) == Decimal(bank['amount']) for n in re.findall(r'\b\d+(?:\.\d+)?\b', note)):
                add('cash_conversion', [identifier], 'unresolved', 'cash_amount_not_supported', purpose=record['source'].get('note'))
                continue
            add('cash_conversion', [identifier], 'confirmed' if atm else 'source_asserted',
                'explicit_atm_withdrawal' if atm else 'actual_bank_debit_and_explicit_cash_received_note',
                purpose=record['source'].get('note'), details={'cash_movement': {'direction': 'in', 'amount': bank['amount']},
                    'cash_evidence': 'bank_withdrawal' if atm else 'user_reported', 'not_current_cash_balance': True})
            classify(identifier, 'cash_conversion', False, False, 'Bank holding converted to cash; cash spending not inferred.')
            continue
        lending = direction == 'debit' and bool(re.search(r'will (?:send|pay|give) (?:it |me )?back|lent|loaned|temporarily', note))
        borrowing = direction == 'credit' and bool(re.search(r'\bborrowed\b|loan from', note))
        if lending or borrowing:
            kind = 'loan_advance' if lending else 'loan_borrowing'
            add(kind, [identifier], 'identified', 'explicit_source_loan_intent_and_bank_direction',
                purpose=record['source'].get('note'), details={'repayment_assumed': False})
            classify(identifier, kind, False, False, 'Principal movement, not purchase, income or completed repayment.')
            continue
        if direction == 'credit' and repayment_note(note):
            classify(identifier, 'reimbursement_received', False, False, 'Source explicitly identifies expense repayment; not new income.')
            candidates = []
            claim_parties = {}
            weak_candidates = []
            for expense_id, expense in records.items():
                debit = expense['bank']; original = expense['event']
                if debit['direction'] != 'debit' or not after(debit, bank) or not original:
                    continue
                for component in original['components']:
                    party = text(component.get('party')); amount = component.get('amount')
                    # AI component is only a proposal: corroborate name and amount in raw notes.
                    if component['kind'] != 'receivable' or not party or not amount:
                        continue
                    if mentions_party(party, note) and mentions_party(party, expense['note']):
                        weak_candidates.append(expense_id)
                        amounts = [Decimal(n) for n in re.findall(r'\b\d+(?:\.\d+)?\b', expense['note'])]
                        if amount == bank['amount'] and Decimal(amount) in amounts:
                            candidates.append(expense_id)
                            claim_parties[expense_id] = party
                if 'train ticket' in note and 'train ticket' in expense['note']:
                    weak_candidates.append(expense_id)
            candidates = sorted(set(candidates))
            # Avoid assigning multiple credits to the same expense/person/amount claim.
            rivals = [other for other, r in records.items() if other != identifier
                and r['bank']['direction'] == 'credit' and r['bank']['amount'] == bank['amount']
                and r['note'] == note and repayment_note(r['note'])]
            if len(candidates) == 1 and not rivals:
                add('reimbursement', [candidates[0], identifier], 'confirmed',
                    'actual_credit_matches_note_supported_expense_share', purpose=record['source'].get('note'),
                    details={'credit_amount': bank['amount'], 'does_not_close_obligation': True})
                relationships[-1]['details']['claim_party'] = claim_parties[candidates[0]]
            else:
                add('reimbursement', [identifier], 'unresolved', 'expense_link_not_unique_or_amount_not_supported',
                    purpose=record['source'].get('note'), candidate_ids=set(candidates + weak_candidates + rivals),
                    details={'receipt_classification_supported': True, 'allocation_unknown': True})
            continue
        if direction == 'debit' and event and event['components']:
            receivables = [c for c in event['components'] if c['kind'] == 'receivable']
            supported = any(mentions_party(text(c.get('party')), note) for c in receivables if c.get('party'))
            expected = bool(re.search(r'share|will send|pay me back|supposed to give', note))
            if receivables and supported and expected:
                add('reimbursement_expected', [identifier], 'identified', 'source_supported_shared_or_recoverable_expense',
                    purpose=record['source'].get('note'), details={'claims_are_unverified_event_components': True,
                        'source_ambiguities': event['ambiguities'], 'no_outstanding_balance_computed': True})
                classify(identifier, 'recoverable_or_shared_expense', False, None,
                    'Do not treat gross payment as personal expense; allocation belongs to 3C.')
                continue
        if direction == 'credit' and re.search(r'my salary.*(?:credit|received)', note):
            classify(identifier, 'salary_income', True, False, 'Actual bank credit with explicit salary-receipt note.')
        elif re.search(r'wanted me to upi|paid for smtg of his', note):
            add('payment_on_behalf', [identifier], 'unresolved', 'beneficiary_known_repayment_terms_unknown',
                purpose=record['source'].get('note'))
        elif direction == 'credit' and re.search(r'sent me money to book', note):
            add('purpose_specific_funding', [identifier], 'source_asserted',
                'explicit_funding_purpose_terms_unspecified', purpose=record['source'].get('note'),
                details={'gift_or_loan_not_assumed': True, 'expense_allocation_not_inferred': True})
        # All other movements stay unclassified; do not turn arbitrary credits into income.

    # Identify potential returns only where a real credit exists. No outstanding/closure state.
    for identifier, treatment in list(treatments.items()):
        if treatment['classification'] not in ('loan_advance', 'loan_borrowing'):
            continue
        bank = records[identifier]['bank']; key = counterparty_key(bank['raw_description'])
        candidates = [other for other, record in records.items()
            if other != identifier and record['bank']['direction'] != bank['direction']
            and record['bank']['amount'] == bank['amount'] and after(bank, record['bank'])
            and key and counterparty_key(record['bank']['raw_description']) == key
            and treatments[other]['classification'] == 'unclassified']
        if candidates:
            add('possible_loan_return', [identifier], 'unresolved', 'bank_counterparty_amount_timing_candidate_only',
                candidate_ids=candidates, details={'repayment_not_confirmed': True})

    # Several equally plausible credits cannot all consume the same source share.
    # This is link integrity, not an outstanding-balance/partial-settlement ledger.
    claims = Counter((r['transaction_ids'][0], r['details'].get('claim_party'),
                      r['details'].get('credit_amount')) for r in relationships
                     if r['type'] == 'reimbursement' and r['status'] == 'confirmed')
    for relationship in relationships:
        if relationship['type'] == 'reimbursement' and relationship['status'] == 'confirmed':
            key = (relationship['transaction_ids'][0], relationship['details']['claim_party'],
                   relationship['details']['credit_amount'])
            if claims[key] > 1:
                relationship.update(status='unresolved', confidence='low', rule='competing_credits_for_same_share')
    # IDs include final status and rule, independent of processing order.
    for relationship in relationships:
        old_id = relationship['relationship_id']
        identity = {k: relationship[k] for k in ('type', 'transaction_ids', 'status', 'rule')}
        relationship['relationship_id'] = 'REL_' + digest(identity)[:16]
        for treatment in treatments.values():
            treatment['relationship_ids'] = [relationship['relationship_id'] if r == old_id else r
                                             for r in treatment['relationship_ids']]
    relationships.sort(key=lambda r: r['relationship_id'])
    for treatment in treatments.values():
        treatment['relationship_ids'].sort()
    result = {'schema_version': '3B.1', 'source_fingerprints': {d['bank']: digest(d) for d in sorted(documents, key=lambda d: d['bank'])},
        'relationships': relationships, 'transaction_treatments': [treatments[i] for i in sorted(treatments)],
        'report': {'transactions': len(records), 'relationships': len(relationships),
                   'by_status': dict(sorted(Counter(r['status'] for r in relationships).items())),
                   'by_type': dict(sorted(Counter(r['type'] for r in relationships).items())),
                   'classifications': dict(sorted(Counter(t['classification'] for t in treatments.values()).items()))},
        'limitations': ['Only explicit supported rules confirm relationships; free-text understanding is not exhaustive.',
            'Income/expense null means unknown or allocation deferred, not zero.',
            'Relationship amounts are evidence, not additional cashflows. Cash movements are not current cash holdings.',
            'No outstanding amounts, partial settlement accounting or closure; possible returns remain candidates.',
            'Dates are day resolution; cross-bank same-day intraday order is not asserted.']}
    validate_relationships(result, records)
    return result


def validate_relationships(result, records):
    validate_schema(RelationshipsOutput, result, "relationships output")
    ids = [r['relationship_id'] for r in result['relationships']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate relationship')
    treatments = result['transaction_treatments']
    if len(treatments) != len(records) or {t['transaction_id'] for t in treatments} != set(records):
        raise ValueError('transaction coverage mismatch')
    for relationship in result['relationships']:
        if relationship['status'] not in ('confirmed', 'identified', 'source_asserted', 'planned', 'unresolved'):
            raise ValueError('invalid relationship status')
        if len(relationship['transaction_ids']) != len(set(relationship['transaction_ids'])):
            raise ValueError('duplicate relationship participant')
        if not set(relationship['transaction_ids'] + relationship['candidate_transaction_ids']) <= set(records):
            raise ValueError('unknown linked transaction')
        if relationship['type'] == 'self_transfer' and relationship['status'] == 'confirmed':
            a, b = [records[i]['bank'] for i in relationship['transaction_ids']]
            if a['amount'] != b['amount'] or a['direction'] == b['direction'] or a['bank'] == b['bank']:
                raise ValueError('unbalanced self-transfer')
    for treatment in treatments:
        if not set(treatment['relationship_ids']) <= set(ids):
            raise ValueError('invalid relationship provenance')
        if treatment['income'] is True and records[treatment['transaction_id']]['bank']['direction'] != 'credit':
            raise ValueError('debit classified as income')
        if treatment['classification'] in ('self_transfer', 'cash_conversion', 'loan_advance', 'loan_borrowing', 'reimbursement_received'):
            if treatment['income'] is not False or treatment['expense'] is not False:
                raise ValueError('principal/holding/reimbursement movement counted as income or expense')
        if treatment['classification'] == 'unclassified' and (treatment['income'] is not None or treatment['expense'] is not None):
            raise ValueError('unknown movement silently counted or excluded')
        expected_ids = {r['relationship_id'] for r in result['relationships']
                        if treatment['transaction_id'] in r['transaction_ids']}
        if set(treatment['relationship_ids']) != expected_ids:
            raise ValueError('relationship provenance coverage mismatch')


def run(paths, output):
    paths = list(map(Path, paths)); snapshots = {p: p.read_bytes() for p in paths}
    documents = [parse_document(snapshots[p], ReconciliationOutput, str(p)) for p in paths]
    result = derive(documents)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    validate_relationships(json.loads(serialized), flatten(documents))
    if any(p.read_bytes() != b for p, b in snapshots.items()):
        raise ValueError('source changed during relationship detection')
    output = Path(output)
    if output.resolve() in {p.resolve() for p in paths}:
        raise ValueError('output cannot overwrite source')
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.json.tmp')
    try:
        temp.write_text(serialized, encoding='utf-8'); temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    return result['report']


if __name__ == '__main__':
    print(json.dumps(run([ROOT / f'output/reconciled/{b}_reconciled.json' for b in ('kotak', 'sbi')],
                         ROOT / 'output/relationships/relationships.json'), indent=2))
