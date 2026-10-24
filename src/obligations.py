"""3C.2: supported obligation origins and unanswered clarifications, no settlements."""

from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .classification import build as classify
from .reconcile import digest
from .relationships import flatten, mentions_party, text

ROOT = Path(__file__).resolve().parents[1]
NUMBER = r'(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?!\w|\.\d)'


def amounts(value):
    return sorted({format(Decimal(n.replace(',', '')), '.2f') for n in re.findall(NUMBER, value)})


def party_pattern(party):
    # Permit the possessives present in notes, but never fuzzy-match unrelated names.
    return r'(?<!\w)' + re.escape(text(party)) + r"(?:s's|'s|s)?(?!\w)"


def explicit_payable(party, note):
    return bool(party and re.search(r'\bi owe\s+' + party_pattern(party), note))


def receivable_party_supported(party, note, single_party):
    p = party_pattern(party)
    if re.search(p + r'\s+(?:share\b|will (?:send|pay|give)\b|is supposed to give\b)', note):
        return True
    if 'share' in note and re.search(p + r'\s+' + NUMBER, note):
        return True  # Explicit share list with omitted repeated word "share".
    if re.search(r'rest they will send', note) and re.search(NUMBER + r'\s+' + p, note):
        return True  # Claim exists; reported receipt is not necessarily principal.
    return bool(single_party and mentions_party(text(party), note) and
                re.search(r"pay me back|supposed to give|will (?:send|pay|give)", note))


def share_amount(record, party, components, single_party):
    """Corroborate original principal, not a receipt amount or a guessed gross share."""
    note, follow = record['note'], record['follow_up']
    proposed = sorted({c['amount'] for c in components if c.get('amount')})
    p = party_pattern(party)
    found = []
    for value in (note, follow):
        patterns = [p + r"\s*(?:share|owes?|is supposed to give(?: cash)?(?: of)?|for the [^:;]+:)?\s*[:=]?\s*(" + NUMBER + ')',
                    r'\bi owe\s+' + p + r'\s+(' + NUMBER + ')']
        for pattern in patterns:
            found.extend(format(Decimal(m.replace(',', '')), '.2f') for m in re.findall(pattern, value))
    # A single counterparty's follow-up may have intervening purpose text.
    if single_party and mentions_party(text(party), follow) and re.search(r'\btake cash from\b', follow):
        found.extend(amounts(follow))
    supported = sorted(set(found))
    if len(supported) == 1 and not record['event']['ambiguities'] and (not proposed or proposed == supported):
        return supported[0], 'explicit_counterparty_principal', proposed, supported
    # Full reimbursement is supported only by an explicit whole purchase for the
    # named beneficiary and repayment promise. Shared or partial payments do not qualify.
    full = (single_party and re.search(r'\bfor\s+' + p + r",?\s+they(?:'ll| will) pay me back\b", note)
            and not re.search(r'\bshare|\bpart|\bsome|\bhalf', note))
    if full and not record['event']['ambiguities'] and proposed == [record['bank']['amount']]:
        return record['bank']['amount'], 'whole_beneficiary_purchase_repayment_explicit', proposed, supported
    return None, 'original_principal_requires_clarification', proposed, supported


def identity(identifier, direction, party):
    # Amount/status changes do not change identity; this is an origin/party claim.
    return 'OBL_' + digest([identifier, direction, text(party)])[:20]


def summarize(origins, questions):
    totals = {}
    for direction in ('receivable', 'payable'):
        totals[direction] = format(sum((Decimal(o['original_amount']) for o in origins
                                       if o['direction'] == direction and o['original_amount'] is not None), Decimal(0)), '.2f')
    return {'confirmed_origins': len(origins),
            'by_type': dict(sorted(Counter(o['type'] for o in origins).items())),
            'by_direction': dict(sorted(Counter(o['direction'] for o in origins).items())),
            'known_original_principal_INR': totals,
            'unknown_original_amount': sum(o['original_amount'] is None for o in origins),
            'origins_requiring_clarification': sum(o['requires_clarification'] for o in origins),
            'clarification_candidates': len(questions),
            'settlement_status': 'UNASSESSED',
            'totals_are_outstanding_balances': False}


def build(documents, relationships, classification, audit):
    records = flatten(documents)
    if classify(documents, relationships) != classification:
        raise ValueError('stale or altered effective classification')
    audited = {r['transaction_id']: r for r in audit['transactions']}
    effective = {r['transaction_id']: r for r in classification['transactions']}
    if len(audited) != len(audit['transactions']) or set(audited) != set(records):
        raise ValueError('audit transaction coverage mismatch')
    for identifier, r in records.items():
        a = audited[identifier]
        if (any(a[k] != v for k, v in r['bank'].items()) or
            a['event_id'] != (r['event']['event_id'] if r['event'] else None) or
            a['effective_treatment'] != effective[identifier]['effective']['treatment']):
            raise ValueError('stale audit evidence')
    origins, questions = [], []
    all_relationships = relationships['relationships']

    def evidence(identifier):
        r = records[identifier]
        return {'source_id': r['source'].get('source_id'), 'note': r['source'].get('note'),
                'follow_up': r['source'].get('follow_up'),
                'bank_transaction_sha256': digest(r['bank']),
                'semantic_event_sha256': digest(r['event']), 'source_row_sha256': digest(r['source'])}

    def clarify(identifier, reason, question, obligation_ids=()):
        r = records[identifier]
        questions.append({'clarification_id': 'CLR_' + digest([identifier, reason])[:20],
            'transaction_ids': [identifier], 'event_ids': [r['event']['event_id']],
            'obligation_ids': sorted(obligation_ids), 'reason': reason, 'question': question,
            'status': 'AWAITING_USER', 'priority': 'normal', 'answer': None,
            'requested_fields': ['obligation_exists', 'direction', 'counterparty', 'original_amount', 'repayment_terms'],
            'evidence': evidence(identifier),
            'relationship_ids': sorted(rel['relationship_id'] for rel in all_relationships if identifier in rel['transaction_ids'])})

    def origin(identifier, direction, kind, party, amount, rule, supporting, proposals=None, supported=None):
        r = records[identifier]
        related = [rel for rel in all_relationships if identifier in rel['transaction_ids'] + rel['candidate_transaction_ids']]
        item = {'obligation_id': identity(identifier, direction, party), 'type': kind, 'direction': direction,
            'counterparty': party, 'purpose': r['source'].get('note'), 'currency': 'INR',
            'currency_basis': 'owned_INR_bank_statement_scope', 'original_amount': amount,
            'origin_date': r['bank']['transaction_date'], 'origin_status': 'CONFIRMED',
            'settlement_status': 'UNASSESSED', 'requires_clarification': amount is None or party is None,
            'originating_transaction_ids': [identifier], 'semantic_event_ids': [r['event']['event_id']],
            'supporting_relationship_ids': sorted(supporting),
            'existing_relationship_context': [{'relationship_id': rel['relationship_id'],
                'origin_role': 'participant' if identifier in rel['transaction_ids'] else 'candidate',
                'transaction_ids': sorted(rel['transaction_ids']),
                'candidate_transaction_ids': sorted(rel['candidate_transaction_ids']),
                'settlement_effect': 'NOT_ASSESSED'} for rel in sorted(related, key=lambda x: x['relationship_id'])],
            'confidence': 'high' if amount is not None and party else 'medium',
            'evidence': dict(evidence(identifier), amount_rule=rule,
                proposed_event_amounts=sorted(proposals or []), source_supported_amount_mentions=sorted(supported or [])),
            'provenance': {'stage': '3C.2', 'effective_treatment': effective[identifier]['effective']['treatment']}}
        origins.append(item)
        return item

    for identifier in sorted(records):
        r = records[identifier]; event = r['event']; treatment = effective[identifier]['effective']['treatment']
        if not event or not r['note']:
            continue  # No invented obligations for missing/blank notes.
        active = [rel for rel in all_relationships if identifier in rel['transaction_ids']]
        if any(rel['type'] in ('payment_on_behalf', 'purpose_specific_funding') for rel in active):
            question = (f"For {identifier}, was the money repayable, a gift, or funding with another arrangement?"
                        if r['bank']['direction'] == 'credit' else
                        f"For {identifier}, did someone agree to repay you? If so, who and how much?")
            clarify(identifier, 'obligation_existence_and_terms_unknown', question)
            continue
        if treatment in ('self_transfer', 'cash_conversion', 'reimbursement_received', 'salary_income'):
            continue
        # Do not promote a positive phrase embedded in a denial or disputed loan.
        if re.search(r"\b(?:not a loan|no repayment|not expected to repay|do not owe|don't owe|owe nothing|owe .* nothing)\b", r['note']):
            if any(rel['type'] in ('loan_advance', 'loan_borrowing', 'reimbursement_expected') for rel in active):
                clarify(identifier, 'conflicting_obligation_evidence', 'Was there an agreement to repay this money, or was it non-repayable?')
            continue
        loans = [rel for rel in active if rel['type'] in ('loan_advance', 'loan_borrowing') and rel['status'] == 'identified']
        if loans:
            direction = 'receivable' if treatment == 'loan_advance' else 'payable'
            if treatment not in ('loan_advance', 'loan_borrowing'):
                raise ValueError('loan relationship lacks matching effective treatment')
            party = event['payee'] if direction == 'receivable' else event['payer']
            if not party or not mentions_party(text(party), r['note']):
                party = None
            # Existing 3B classification supplies explicit repayment intent, never event_type alone.
            numbers = amounts(r['note'])
            amount = r['bank']['amount'] if not numbers or numbers == [r['bank']['amount']] else None
            item = origin(identifier, direction, 'loan_advance' if direction == 'receivable' else 'loan_borrowing',
                          party, amount, 'explicit_loan_intent_and_bank_principal' if amount else 'partial_or_conflicting_loan_amount',
                          [rel['relationship_id'] for rel in loans])
            if item['requires_clarification']:
                clarify(identifier, 'loan_principal_or_party_unknown', 'Who is the lender/borrower and what was the original repayable amount?', [item['obligation_id']])
            continue
        expected = [rel for rel in active if rel['type'] == 'reimbursement_expected' and rel['status'] == 'identified']
        groups = {}
        receivable_parties = {text(c.get('party')) for c in event['components'] if c['kind'] == 'receivable' and c.get('party')}
        for component in event['components']:
            party = component.get('party')
            if not party or not mentions_party(text(party), r['note']):
                continue
            direction = ('receivable' if expected and component['kind'] == 'receivable' and
                         receivable_party_supported(party, r['note'], len(receivable_parties) == 1) else
                         'payable' if component['kind'] == 'payable' and explicit_payable(party, r['note']) else None)
            if direction:
                groups.setdefault((direction, text(party)), []).append(component)
        made = []
        for (direction, _), components in sorted(groups.items()):
            party = sorted({c['party'] for c in components})[0]
            amount, rule, proposals, supported = share_amount(r, party, components, len(groups) == 1)
            if direction == 'receivable' and amount is not None and Decimal(amount) > Decimal(r['bank']['amount']):
                amount, rule = None, 'share_exceeds_origin_amount'
            item = origin(identifier, direction, 'reimbursement' if direction == 'receivable' else 'explicit_payable',
                          party, amount, rule, [rel['relationship_id'] for rel in expected] if direction == 'receivable' else [], proposals, supported)
            made.append(item)
        # Known individual shares cannot collectively exceed the supported expense.
        if sum((Decimal(o['original_amount']) for o in made if o['direction'] == 'receivable' and o['original_amount']), Decimal(0)) > Decimal(r['bank']['amount']):
            for item in made:
                if item['direction'] == 'receivable':
                    item.update(original_amount=None, requires_clarification=True, confidence='medium')
                    item['evidence']['amount_rule'] = 'combined_shares_exceed_origin_amount'
        uncertain = [o['obligation_id'] for o in made if o['requires_clarification']]
        if uncertain:
            clarify(identifier, 'original_share_amount_unclear',
                    f"For {identifier}, what was each person's original repayable share, before any reimbursements? Please resolve any differing note/follow-up amounts.", uncertain)
        elif expected and not made:
            clarify(identifier, 'recoverable_party_or_terms_unsupported', 'Who agreed to reimburse this expense, and what was their original share?')
    origins.sort(key=lambda o: o['obligation_id']); questions.sort(key=lambda q: q['clarification_id'])
    result = {'schema_version': '3C.2.1',
        'input_fingerprints': {'reconciled': {d['bank']: digest(d) for d in sorted(documents, key=lambda x: x['bank'])},
                               'relationships': digest(relationships), 'classification': digest(classification), 'audit': digest(audit)},
        'obligations': origins, 'clarification_candidates': questions, 'report': summarize(origins, questions),
        'policy': {'settlements_evaluated': False, 'amounts_are_original_principal_only': True,
                   'known_totals_exclude_unknown_principals': True,
                   'clarification_updates': 'Future answers must retain question ID, respondent, timestamp, raw answer and prior interpretation hash; append an interpretation revision, never alter bank facts or silently replace original events.'},
        'limitations': ['All settlement statuses are UNASSESSED, including historical claims with existing receipt links.',
                        'No new receipt matching, deductions, residuals, closure or OPEN status.',
                        'Bounded source-backed rules, not exhaustive natural-language understanding.',
                        'Names identify source-level counterparties, not globally resolved people; parents is an unallocated group.',
                        'INR is the explicit scope of these owned bank statements; other currencies require schema/input extension.']}
    validate(result, records, relationships)
    return result


def validate(result, records, relationships):
    ids, keys = set(), set()
    rels = {r['relationship_id']: r for r in relationships['relationships']}
    for o in result['obligations']:
        identifier, = o['originating_transaction_ids']
        r = records[identifier]
        key = (identifier, o['direction'], text(o['counterparty']))
        if o['obligation_id'] in ids or key in keys or o['obligation_id'] != identity(*key):
            raise ValueError('duplicate or unstable obligation identity')
        ids.add(o['obligation_id']); keys.add(key)
        if o['direction'] not in ('receivable', 'payable') or o['currency'] != 'INR':
            raise ValueError('unsupported obligation direction/currency')
        allowed_directions = {'loan_advance': 'receivable', 'reimbursement': 'receivable',
                              'loan_borrowing': 'payable', 'explicit_payable': 'payable'}
        if allowed_directions.get(o['type']) != o['direction']:
            raise ValueError('obligation type/direction mismatch')
        if o['settlement_status'] != 'UNASSESSED' or o['origin_status'] != 'CONFIRMED':
            raise ValueError('settlement/lifecycle is outside 3C.2')
        if o['semantic_event_ids'] != [r['event']['event_id']] or not r['source'].get('note'):
            raise ValueError('missing origin meaning')
        if o['direction'] == 'receivable' and r['bank']['direction'] != 'debit':
            raise ValueError('receivable must originate from an evidenced debit')
        if o['type'] == 'loan_borrowing' and r['bank']['direction'] != 'credit':
            raise ValueError('borrowing must originate from credit')
        amount = o['original_amount']
        if amount is not None and (not re.fullmatch(r'\d+\.\d{2}', amount) or Decimal(amount) <= 0):
            raise ValueError('invalid original amount')
        if amount is not None:
            rule = o['evidence']['amount_rule']
            if rule in ('explicit_loan_intent_and_bank_principal', 'whole_beneficiary_purchase_repayment_explicit'):
                if amount != r['bank']['amount']:
                    raise ValueError('principal differs from corroborated bank amount')
            elif rule == 'explicit_counterparty_principal':
                if o['evidence']['source_supported_amount_mentions'] != [amount]:
                    raise ValueError('principal differs from source-supported amount')
            else:
                raise ValueError('known principal has no supported amount rule')
            if o['direction'] == 'receivable' and Decimal(amount) > Decimal(r['bank']['amount']):
                raise ValueError('recoverable principal exceeds origin')
        if (amount is None or o['counterparty'] is None) and not o['requires_clarification']:
            raise ValueError('unknown principal/party not flagged')
        if any(k in o for k in ('settled_amount', 'outstanding_amount', 'closed_at')):
            raise ValueError('settlement fields are outside scope')
        if not set(o['supporting_relationship_ids']) <= set(rels):
            raise ValueError('unknown supporting relationship')
        for rel_id in o['supporting_relationship_ids']:
            if identifier not in rels[rel_id]['transaction_ids']:
                raise ValueError('relationship does not support origin')
        expected_context = [{'relationship_id': rel['relationship_id'],
            'origin_role': 'participant' if identifier in rel['transaction_ids'] else 'candidate',
            'transaction_ids': sorted(rel['transaction_ids']), 'candidate_transaction_ids': sorted(rel['candidate_transaction_ids']),
            'settlement_effect': 'NOT_ASSESSED'} for rel in sorted(rels.values(), key=lambda x: x['relationship_id'])
            if identifier in rel['transaction_ids'] + rel['candidate_transaction_ids']]
        if o['existing_relationship_context'] != expected_context:
            raise ValueError('relationship context changed or interpreted as settlement')
        for key in ('note', 'follow_up', 'source_id'):
            if o['evidence'][key] != r['source'].get(key):
                raise ValueError('source evidence changed')
        for field, original in [('bank_transaction_sha256', r['bank']), ('semantic_event_sha256', r['event']), ('source_row_sha256', r['source'])]:
            if o['evidence'][field] != digest(original):
                raise ValueError('origin provenance changed')
    question_ids = [q['clarification_id'] for q in result['clarification_candidates']]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError('duplicate clarification')
    for q in result['clarification_candidates']:
        if q['answer'] is not None or q['status'] != 'AWAITING_USER' or not set(q['obligation_ids']) <= ids:
            raise ValueError('invalid unanswered clarification')
        identifier, = q['transaction_ids']
        if q['event_ids'] != [records[identifier]['event']['event_id']] or q['clarification_id'] != 'CLR_' + digest([identifier, q['reason']])[:20]:
            raise ValueError('clarification identity or event link changed')
    questioned = {i for q in result['clarification_candidates'] for i in q['obligation_ids']}
    if any(o['requires_clarification'] and o['obligation_id'] not in questioned for o in result['obligations']):
        raise ValueError('unresolved origin lacks clarification candidate')
    if result['report'] != summarize(result['obligations'], result['clarification_candidates']):
        raise ValueError('incorrect principal summary')


def run(root=ROOT):
    root = Path(root)
    protected = sorted([p for folder in ('input', 'output/bank', 'output/events', 'output/reconciled',
        'output/relationships', 'output/classification') for p in (root / folder).rglob('*') if p.is_file()]
        + [root / 'output/audit/coverage_audit.json'])
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    def read(path):
        return json.loads((root / path).read_text(encoding='utf-8'))
    audit = read('output/audit/coverage_audit.json')
    for name, expected in audit['verification']['source_sha256'].items():
        if hashes.get(name) != expected:
            raise ValueError('audit source snapshot is stale: ' + name)
    documents = [read(f'output/reconciled/{b}_reconciled.json') for b in ('kotak', 'sbi')]
    relationships = read('output/relationships/relationships.json')
    result = build(documents, relationships, read('output/classification/effective_transactions.json'), audit)
    result['verification'] = {'protected_file_sha256': hashes, 'source_files_unchanged': True}
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    validate(json.loads(encoded), flatten(documents), relationships)
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != hashes[str(p.relative_to(root))] for p in protected):
        raise ValueError('source changed during obligation generation')
    output = root / 'output/obligations/obligations.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.json.tmp')
    try:
        temporary.write_text(encoded, encoding='utf-8'); temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return result


if __name__ == '__main__':
    print(json.dumps(run()['report'], indent=2))
