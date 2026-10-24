"""3C.3: evidence-backed repayment allocations and conservative settlement views."""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .obligations import build as build_origins, party_pattern
from .reconcile import digest
from .relationships import counterparty_key, flatten, mentions_party, text

ROOT = Path(__file__).resolve().parents[1]
ZERO = Decimal('0.00')
TOLERANCE = Decimal('5.00')
EXCLUDED = {'self_transfer', 'cash_conversion', 'salary_income', 'loan_advance', 'loan_borrowing',
            'recoverable_or_shared_expense', 'expense'}
AMOUNT = r'\d+(?:,\d{3})*(?:\.\d{1,2})?'


def money(value):
    return format(Decimal(str(value).replace(',', '')), '.2f')


def chronological(origin, repayment):
    a, b = origin['bank'], repayment['bank']
    if b['transaction_date'] < a['transaction_date']:
        return False
    return not (a['transaction_date'] == b['transaction_date'] and a['bank'] == b['bank'] and
                b['transaction_id'] <= a['transaction_id'])


def repayment_language(note):
    if re.search(r"\b(?:not repaid|never repaid|not reimbursed|will repay|plan to repay|should repay|need to repay)\b", note):
        return False
    return bool(re.search(r'\b(?:repaid|reimbursed|paid back|returned.*(?:loan|advance)|(?:sent|paid).*share)\b', note))


def full_intent(note):
    return bool(re.search(r'\b(?:repaid in full|full settlement|fully repaid|final repayment|nothing more owed)\b', note)) and not bool(
        re.search(r'\b(?:not fully|not repaid|partial|still owe|still owed|remaining balance|outstanding balance)\b', note))


def proof(record):
    return {'transaction_id': record['bank']['transaction_id'],
            'bank_transaction_sha256': digest(record['bank']),
            'source_id': record['source'].get('source_id'), 'source_row_sha256': digest(record['source']),
            'semantic_event_id': record['event']['event_id'] if record['event'] else None,
            'note': record['source'].get('note'), 'follow_up': record['source'].get('follow_up')}


def balance_assertion(origin, payments, records, as_of):
    """Explicit dated source statements, never an inferred complete-history flag."""
    found = []
    ids = origin['originating_transaction_ids'] + [p['repayment_transaction_id'] for p in payments]
    for identifier in sorted(set(ids)):
        record = records[identifier]
        for field in ('note', 'follow_up'):
            source = text(record['source'].get(field))
            patterns = [(r'no repayments received as of (\d{4}-\d{2}-\d{2})', 'zero'),
                        (r'as of (\d{4}-\d{2}-\d{2}),? outstanding balance is (' + AMOUNT + r')(?![\d.])', 'balance')]
            for pattern, kind in patterns:
                for match in re.finditer(pattern, source):
                    day = date.fromisoformat(match[1]).isoformat()
                    if day >= as_of:
                        found.append({'kind': kind, 'amount': None if kind == 'zero' else money(match[2]),
                                      'as_of': day, 'transaction_id': identifier, 'source_field': field,
                                      'source_excerpt': match[0]})
    # Conflicting or generic statements across multiple claims must not be guessed.
    if found:
        if len({(a['kind'], a['amount'], a['as_of']) for a in found}) == 1:
            return found[0]
        return {'kind': 'conflict', 'statements': found}
    return None


def assessment(origin, payments, records, as_of, unresolved=False, allow_assertion=True):
    total = sum((Decimal(p['amount']) for p in payments), ZERO)
    principal = Decimal(origin['original_amount']) if origin['original_amount'] is not None else None
    residual = principal - total if principal is not None else None
    result = {'obligation_id': origin['obligation_id'], 'origin_sha256': digest(origin),
        'direction': origin['direction'], 'currency': origin['currency'], 'original_principal': origin['original_amount'],
        'verified_repayments': money(total), 'verified_zero_repayments': False,
        'adjustment': '0.00', 'adjustment_evidence': None,
        'unadjusted_residual': money(residual) if residual is not None else None,
        'outstanding_amount': None, 'status': 'UNASSESSED', 'as_of': as_of,
        'reason': 'historical_coverage_or_repayment_evidence_incomplete',
        'repayment_transaction_ids': sorted({p['repayment_transaction_id'] for p in payments}),
        'allocation_ids': sorted(p['allocation_id'] for p in payments), 'balance_assertion': None}
    if principal is None:
        result['reason'] = 'original_principal_unknown'
        return result
    if unresolved:
        result['reason'] = 'unresolved_repayment_or_allocation_evidence'
        return result
    assertion = balance_assertion(origin, payments, records, as_of) if allow_assertion else None
    if assertion and (assertion['kind'] == 'conflict' or assertion['kind'] == 'zero' and payments or
                      assertion['kind'] == 'balance' and Decimal(assertion['amount']) != residual):
        result.update(reason='dated_balance_statement_conflicts_with_ledger', balance_assertion=assertion)
        return result
    latest = []
    if payments:
        last_day = max(records[p['repayment_transaction_id']]['bank']['transaction_date'] for p in payments)
        latest = [p for p in payments if records[p['repayment_transaction_id']]['bank']['transaction_date'] == last_day]
        if len({records[p['repayment_transaction_id']]['bank']['bank'] for p in latest}) == 1:
            latest = sorted(latest, key=lambda p: p['repayment_transaction_id'])[-1:]
    # Full intent must apply to the final repayment, not an earlier installment.
    # Cross-bank dates do not supply intraday order: every possible last payment
    # must support final settlement before applying tolerance.
    final_full = bool(latest and all(p['full_settlement_intent'] for p in latest))
    if payments and residual == ZERO:
        result.update(status='CLOSED', outstanding_amount='0.00', reason='verified_principal_repaid_exactly')
    elif payments and final_full and ZERO < residual <= TOLERANCE:
        result.update(status='CLOSED', outstanding_amount='0.00', adjustment=money(residual),
                      adjustment_evidence={'allocation_ids': sorted(p['allocation_id'] for p in latest), 'rule': 'supported_final_residual_up_to_5'},
                      reason='minor_final_residual_adjustment')
    elif payments and residual < ZERO:
        # Even tiny unexplained excesses cannot be silently written off.
        if final_full and abs(residual) <= TOLERANCE and all(p['explicit_minor_overpayment'] for p in latest):
            result.update(status='CLOSED', outstanding_amount='0.00', adjustment=money(residual),
                          adjustment_evidence={'allocation_ids': sorted(p['allocation_id'] for p in latest), 'rule': 'explicit_minor_overpayment_adjustment'},
                          reason='documented_minor_overpayment')
        else:
            result['reason'] = 'unexplained_overpayment'
    else:
        if assertion and assertion['kind'] == 'zero' and not payments:
            result.update(status='OPEN', outstanding_amount=money(principal), verified_zero_repayments=True,
                          balance_assertion=assertion, as_of=assertion['as_of'], reason='dated_explicit_no_repayments_statement')
        elif assertion and assertion['kind'] == 'balance' and Decimal(assertion['amount']) == residual and residual > ZERO:
            result.update(status='PARTIAL' if payments else 'OPEN', outstanding_amount=money(residual),
                          verified_zero_repayments=not payments, balance_assertion=assertion,
                          as_of=assertion['as_of'], reason='dated_explicit_balance_corresponds_to_verified_ledger')
    return result


def summarize(view, allocations, candidates, questions):
    totals = {}
    for direction in ('receivable', 'payable'):
        rows = [v for v in view if v['direction'] == direction]
        totals[direction] = {
            'confirmed_outstanding': money(sum((Decimal(v['outstanding_amount']) for v in rows if v['outstanding_amount'] is not None), ZERO)),
            'unassessed_known_principal': money(sum((Decimal(v['original_principal']) for v in rows
                if v['status'] == 'UNASSESSED' and v['original_principal'] is not None), ZERO)),
            'unassessed_arithmetic_residual_not_confirmed_debt': money(sum((max(Decimal(v['unadjusted_residual']), ZERO) for v in rows
                if v['status'] == 'UNASSESSED' and v['unadjusted_residual'] is not None), ZERO)),
            'unknown_principal_count': sum(v['original_principal'] is None for v in rows)}
    return {'obligations': len(view), 'by_status': {s: sum(v['status'] == s for v in view) for s in ('OPEN', 'PARTIAL', 'CLOSED', 'UNASSESSED')},
            'verified_repayments': money(sum((Decimal(a['amount']) for a in allocations), ZERO)),
            'verified_repayments_by_direction': {d: money(sum((Decimal(v['verified_repayments']) for v in view if v['direction'] == d), ZERO))
                                                for d in ('receivable', 'payable')},
            'outstanding_INR': totals, 'signed_adjustments': money(sum((Decimal(v['adjustment']) for v in view), ZERO)),
            'unresolved_candidate_groups': len(candidates), 'new_clarifications': len(questions)}


def derive(origins, records, relationships, classification):
    """Match once, resolve competing claims globally, then assess final residuals."""
    obligations = {o['obligation_id']: o for o in origins['obligations']}
    effective = {t['transaction_id']: t['effective']['treatment'] for t in classification['transactions']}
    rels = relationships['relationships']
    proposals, candidates = [], []
    origin_txids = {i for o in obligations.values() for i in o['originating_transaction_ids']}

    def compatible(o, record):
        expected_direction = 'credit' if o['direction'] == 'receivable' else 'debit'
        return (record['bank']['direction'] == expected_direction and
                all(chronological(records[i], record) for i in o['originating_transaction_ids']))

    def unresolved(identifier, oids, reason, relationship_ids=()):
        if not oids:
            return
        identity = [identifier, sorted(set(oids)), reason]
        candidates.append({'candidate_id': 'CAND_' + digest(identity)[:20], 'repayment_transaction_id': identifier,
            'obligation_ids': identity[1], 'reason': reason, 'relationship_ids': sorted(relationship_ids),
            'bank_amount': records[identifier]['bank']['amount'], 'status': 'UNRESOLVED', 'evidence': proof(records[identifier])})

    def propose(identifier, oid, amount, rule, linked=(), explicit_split=False):
        record = records[identifier]
        proposals.append({'allocation_id': 'ALLOC_' + digest([identifier, oid])[:20], 'obligation_id': oid,
            'repayment_transaction_id': identifier, 'amount': amount, 'currency': 'INR', 'rule': rule,
            'relationship_ids': sorted(linked), 'explicit_split': explicit_split,
            'full_settlement_intent': full_intent(record['note']),
            'explicit_minor_overpayment': bool(re.search(r'\b(?:extra .*rounding|minor overpayment adjustment)\b', record['note'])),
            'confidence': 'high', 'evidence': proof(record)})

    for identifier in sorted(records):
        record = records[identifier]
        if identifier in origin_txids or effective[identifier] in EXCLUDED:
            continue
        available = [o for o in obligations.values() if compatible(o, record)]
        if not available:
            continue
        # Planned own-account instructions are never repayments, but a real
        # reimbursement receipt with a separate transfer plan may still settle.
        linked = [r for r in rels if r['type'] == 'reimbursement' and identifier in r['transaction_ids']]
        confirmed = []
        for rel in linked:
            if rel['status'] != 'confirmed':
                continue
            for o in available:
                if (set(o['originating_transaction_ids']) <= set(rel['transaction_ids']) and
                    text(o['counterparty']) == text(rel['details'].get('claim_party')) and
                    record['bank']['amount'] == rel['details'].get('credit_amount')):
                    confirmed.append((o, rel))
        if len({o['obligation_id'] for o, _ in confirmed}) == 1:
            o = confirmed[0][0]
            propose(identifier, o['obligation_id'], record['bank']['amount'], 'confirmed_3B_reimbursement',
                    [r['relationship_id'] for _, r in confirmed])
            continue
        if confirmed:
            unresolved(identifier, [o['obligation_id'] for o, _ in confirmed], 'competing_confirmed_links')
            continue
        note = record['note']
        # Bounded explicit allocation syntax in original notes. No FIFO or guessed split.
        explicit = re.findall(r'\b((?:KOTAK|SBI)_\d{4,})\s*[:=]\s*(' + AMOUNT + r')(?![\d.])', record['source'].get('note', ''), re.I)
        markers = re.findall(r'\b((?:KOTAK|SBI)_\d{4,})\s*[:=]', note, re.I)
        if markers and repayment_language(note):
            selected = []
            for txid, amount in explicit:
                matches = [o for o in available if txid.upper() in o['originating_transaction_ids'] and
                           mentions_party(text(o['counterparty']), note)]
                if len(matches) == 1 and Decimal(amount.replace(',', '')) > ZERO:
                    selected.append((matches[0]['obligation_id'], money(amount)))
            if (len(selected) == len(explicit) == len(markers) and len({o for o, _ in selected}) == len(selected) and
                sum((Decimal(a) for _, a in selected), ZERO) <= Decimal(record['bank']['amount'])):
                for oid, amount in selected:
                    propose(identifier, oid, amount, 'explicit_source_allocation', explicit_split=True)
            else:
                unresolved(identifier, [o['obligation_id'] for o in available if any(tx.upper() in o['originating_transaction_ids'] for tx in markers)],
                           'invalid_or_ambiguous_explicit_allocation')
            continue
        # Strong source-origin reference plus named counterparty and repayment language.
        named = [o for o in available if mentions_party(text(o['counterparty']), note)]
        matches = []
        if repayment_language(note):
            for o in named:
                original = records[o['originating_transaction_ids'][0]]
                reference = original['bank']['reference']
                explicit_origin = (any(re.search(r'\b' + re.escape(i.lower()) + r'\b', note) for i in o['originating_transaction_ids']) or
                                   bool(reference and re.search(r'(?<!\w)' + re.escape(reference.lower()) + r'(?!\w)', note)))
                same_kind = ((o['type'] in ('loan_advance', 'loan_borrowing') and re.search(r'\b(?:loan|advance)\b', note)) or
                             (o['type'] == 'explicit_payable' and re.search(r'\b(?:debt|owed)\b', note)))
                if explicit_origin or same_kind:
                    matches.append(o)
            if len(matches) == 1:
                propose(identifier, matches[0]['obligation_id'], record['bank']['amount'], 'explicit_repayment_origin_and_party')
                continue
            if matches:
                unresolved(identifier, [o['obligation_id'] for o in matches], 'repayment_origin_not_unique')
                continue
        # Same-day immediate temporary advance return: exact bank counterparty key,
        # full principal, chronology, explicit immediate-return intent, no rival origin.
        immediate = []
        for o in available:
            original = records[o['originating_transaction_ids'][0]]
            if (o['type'] == 'loan_advance' and o['original_amount'] == record['bank']['amount'] and
                original['bank']['transaction_date'] == record['bank']['transaction_date'] and
                re.search(r'will (?:send|pay|give) it back right away', original['note']) and
                counterparty_key(original['bank']['raw_description']) and
                counterparty_key(original['bank']['raw_description']) == counterparty_key(record['bank']['raw_description']) and
                not note):
                rivals = [other for other in available if other['obligation_id'] != o['obligation_id'] and
                          text(other['counterparty']) == text(o['counterparty'])]
                if not rivals:
                    immediate.append(o)
        if len(immediate) == 1:
            o = immediate[0]
            # Multiple indistinguishable return credits must not be accepted twice.
            rivals = [other for other, rr in records.items() if other != identifier and compatible(o, rr) and
                rr['bank']['transaction_date'] == record['bank']['transaction_date'] and
                rr['bank']['amount'] == record['bank']['amount'] and
                counterparty_key(rr['bank']['raw_description']) == counterparty_key(record['bank']['raw_description'])]
            if not rivals:
                propose(identifier, o['obligation_id'], record['bank']['amount'], 'unique_immediate_bank_counterparty_return')
                continue
        related = [r for r in rels if r['type'] in ('reimbursement', 'possible_loan_return') and
                   identifier in r['transaction_ids'] + r['candidate_transaction_ids']]
        weak = [o['obligation_id'] for o in available if any(
            set(o['originating_transaction_ids']) & set(r['transaction_ids'] + r['candidate_transaction_ids']) for r in related)]
        if weak:
            unresolved(identifier, weak, 'existing_relationship_requires_allocation_or_confirmation', [r['relationship_id'] for r in related])
        elif repayment_language(note) and named:
            unresolved(identifier, [o['obligation_id'] for o in named], 'repayment_purpose_or_origin_insufficient')

    # Globally enforce repayment capacity and unique allocation identity.
    grouped = defaultdict(list)
    for p in proposals:
        grouped[p['repayment_transaction_id']].append(p)
    allocations = []
    for identifier, group in sorted(grouped.items()):
        if (len({p['obligation_id'] for p in group}) != len(group) or
            sum((Decimal(p['amount']) for p in group), ZERO) > Decimal(records[identifier]['bank']['amount']) or
            len(group) > 1 and not all(p['explicit_split'] for p in group)):
            unresolved(identifier, [p['obligation_id'] for p in group], 'repayment_capacity_or_competing_allocations')
        else:
            allocations.extend(group)
    allocations.sort(key=lambda a: a['allocation_id'])
    candidates.sort(key=lambda c: c['candidate_id'])
    as_of = max(r['bank']['transaction_date'] for r in records.values())
    counts = Counter(i for o in obligations.values() for i in o['originating_transaction_ids'])
    view = []
    for oid, o in sorted(obligations.items()):
        payments = [a for a in allocations if a['obligation_id'] == oid]
        row = assessment(o, payments, records, as_of, any(oid in c['obligation_ids'] for c in candidates),
                         allow_assertion=all(counts[i] == 1 for i in o['originating_transaction_ids']))
        row['existing_clarification_ids'] = sorted(q['clarification_id'] for q in origins['clarification_candidates'] if oid in q['obligation_ids'])
        view.append(row)
    questions = []
    for c in candidates:
        questions.append({'clarification_id': 'SETCLR_' + digest(c['candidate_id'])[:20],
            'obligation_ids': c['obligation_ids'], 'repayment_transaction_id': c['repayment_transaction_id'],
            'candidate_id': c['candidate_id'], 'reason': c['reason'], 'status': 'AWAITING_USER', 'answer': None,
            'question': f"Which obligation(s) did {c['repayment_transaction_id']} repay, and how much applied to each?"})
    for row in view:
        if row['status'] == 'UNASSESSED' and not row['existing_clarification_ids'] and not any(row['obligation_id'] in c['obligation_ids'] for c in candidates):
            questions.append({'clarification_id': 'SETCLR_' + digest([row['obligation_id'], row['reason']])[:20],
                'obligation_ids': [row['obligation_id']], 'repayment_transaction_id': None,
                'candidate_id': None, 'reason': row['reason'], 'status': 'AWAITING_USER', 'answer': None,
                'question': 'What amount, if any, remains owed on this obligation, and as of what date? Please identify any repayments.'})
    questions.sort(key=lambda q: q['clarification_id'])
    # Consumer overlay only. Never alter 3C.1 or count allocations as extra flows.
    effects = []
    excess_unresolved = {row['obligation_id'] for row in view if row['reason'] == 'unexplained_overpayment'}
    for identifier in sorted({a['repayment_transaction_id'] for a in allocations}):
        used = [a for a in allocations if a['repayment_transaction_id'] == identifier]
        amount = sum((Decimal(a['amount']) for a in used), ZERO)
        effects.append({'transaction_id': identifier, 'original_effective_treatment': effective[identifier],
            'verified_principal_repayment_amount': money(amount),
            'remaining_transaction_amount_unclassified_by_this_stage': money(Decimal(records[identifier]['bank']['amount']) - amount),
            'repayment_is_income_or_expense': None if any(a['obligation_id'] in excess_unresolved for a in used) else False,
            'excess_treatment_requires_review': any(a['obligation_id'] in excess_unresolved for a in used),
            'allocation_ids': sorted(a['allocation_id'] for a in used),
            'instruction': 'Apply only to the allocated amount of the existing bank movement; never add a cashflow.'})
    return {'allocations': allocations, 'obligation_settlement_view': view, 'unresolved_candidates': candidates,
            'clarification_candidates': questions,
            'carried_origin_clarification_ids': sorted(q['clarification_id'] for q in origins['clarification_candidates']),
            'classification_effects': effects, 'report': summarize(view, allocations, candidates, questions)}


def build(documents, relationships, classification, audit, origins):
    rebuilt = build_origins(documents, relationships, classification, audit)
    if rebuilt != {k: v for k, v in origins.items() if k != 'verification'}:
        raise ValueError('stale or altered obligation origins')
    records = flatten(documents)
    result = {'schema_version': '3C.3.1', 'input_fingerprints': {
        'reconciled': {d['bank']: digest(d) for d in sorted(documents, key=lambda d: d['bank'])},
        'relationships': digest(relationships), 'classification': digest(classification), 'audit': digest(audit), 'origins': digest(origins)},
        **derive(origins, records, relationships, classification),
        'policy': {'tolerance_INR': '5.00', 'tolerance_applies_to': 'final_obligation_residual_with_full_settlement_evidence',
                   'absence_of_match_is_not_verified_zero': True,
                   'unassessed_residuals_are_not_confirmed_outstanding': True},
        'limitations': ['Bounded positive evidence rules; unsupported wording and allocations remain candidates.',
            'OPEN/PARTIAL require a dated explicit balance/no-repayment statement through the supplied bank-data horizon.',
            'Cash/off-bank repayments and unprovided history are not inferred; zero confirmed outstanding is not proof of no debt.',
            'Existing source-level parties are not merged; parents and Dad are not automatically identical for allocation.',
            'No principal revision, new financial event, or modification to any upstream record.']}
    validate(result, origins, records, relationships, classification)
    return result


def validate(result, origins, records, relationships, classification):
    # Re-derive decisions to validate financial evidence as well as output shape.
    expected = derive(origins, records, relationships, classification)
    if any(result[k] != v for k, v in expected.items()):
        raise ValueError('settlement decisions, amounts or provenance differ from source evidence')
    allocations = result['allocations']; ids = [a['allocation_id'] for a in allocations]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate settlement allocation')
    used = defaultdict(lambda: ZERO)
    for a in allocations:
        amount = Decimal(a['amount'])
        if amount <= ZERO:
            raise ValueError('nonpositive repayment allocation')
        used[a['repayment_transaction_id']] += amount
    if any(amount > Decimal(records[i]['bank']['amount']) for i, amount in used.items()):
        raise ValueError('repayment used more than once or overallocated')
    for row in result['obligation_settlement_view']:
        if row['outstanding_amount'] is not None:
            if Decimal(row['original_principal']) - Decimal(row['verified_repayments']) - Decimal(row['adjustment']) != Decimal(row['outstanding_amount']):
                raise ValueError('settlement arithmetic does not balance')
        if abs(Decimal(row['adjustment'])) > TOLERANCE:
            raise ValueError('minor adjustment exceeds final tolerance')


def run(root=ROOT):
    root = Path(root)
    protected = sorted(p for folder in ('input', 'output/bank', 'output/events', 'output/reconciled',
        'output/relationships', 'output/classification', 'output/audit', 'output/obligations')
        for p in (root / folder).rglob('*') if p.is_file())
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    def read(path):
        return json.loads((root / path).read_text(encoding='utf-8'))
    origins = read('output/obligations/obligations.json')
    for name, expected in origins['verification']['protected_file_sha256'].items():
        if hashes.get(name) != expected:
            raise ValueError('stale obligation source snapshot: ' + name)
    docs = [read(f'output/reconciled/{b}_reconciled.json') for b in ('kotak', 'sbi')]
    rels = read('output/relationships/relationships.json'); classification = read('output/classification/effective_transactions.json')
    result = build(docs, rels, classification, read('output/audit/coverage_audit.json'), origins)
    result['verification'] = {'source_sha256': hashes, 'sources_unchanged': True}
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    validate(json.loads(encoded), origins, flatten(docs), rels, classification)
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != hashes[str(p.relative_to(root))] for p in protected):
        raise ValueError('upstream source changed during settlement')
    output = root / 'output/settlements/settlements.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.json.tmp')
    try:
        temporary.write_text(encoded, encoding='utf-8'); temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return result


if __name__ == '__main__':
    print(json.dumps(run()['report'], indent=2))
