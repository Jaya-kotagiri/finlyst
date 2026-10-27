"""Phase 3A: conservative, deterministic bank-to-event matching only."""

import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import re
from pathlib import Path

from .event_models import FinancialEvent
from .output_models import BankTransactions, EventDocument, ReconciliationOutput
from .schema_validation import parse_document, validate_schema
from .validation import validate_transactions

ROOT = Path(__file__).resolve().parents[1]


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(compact(value).encode('utf-8')).hexdigest()


def narration(value):
    # Spreadsheet wrapping can split a word at different positions.
    return ''.join((value or '').split())


def reference_precision(source, authoritative):
    """Recognize decimal rounding, not arbitrary similar reference identifiers."""
    if not isinstance(source, str) or not isinstance(authoritative, str):
        return None
    # Do not reinterpret alphanumeric references or meaningful leading zeros.
    if not re.fullmatch(r'[1-9][0-9]{5,39}', authoritative):
        return None
    text = source.strip()
    if re.fullmatch(r'[1-9](?:\.[0-9]+)?[eE]\+?[0-9]{1,2}', text):
        value = Decimal(text)
        precision = len(value.as_tuple().digits)
        exponent = value.as_tuple().exponent
    elif re.fullmatch(r'[1-9][0-9]{5,39}', text):
        exponent = len(text) - len(text.rstrip('0'))
        precision = len(text) - exponent
        value = Decimal(text)
    else:
        return None
    if precision < 3 or exponent < 0:
        return None
    with localcontext() as context:
        context.prec = 100
        quantum = Decimal(1).scaleb(exponent)
        if Decimal(authoritative).quantize(quantum, rounding=ROUND_HALF_UP) != value:
            return None
    return {'kind': 'decimal_precision_loss_or_formatting',
            'source_reference': source, 'authoritative_reference': authoritative,
            'rounding_quantum': format(quantum, 'f'), 'significant_digits': precision}


def validate_inputs(bank, document):
    validate_transactions(bank)
    validate_schema(EventDocument, document, "reconciliation event input")
    name = document['bank']
    if any(t['bank'] != name for t in bank):
        raise ValueError('bank/event document mismatch')
    rows, events = document['source_rows'], document['events']
    if digest(rows) != document['source_rows_sha256']:
        raise ValueError('source-row fingerprint mismatch')
    if len(rows) != len(events):
        raise ValueError('event/source coverage mismatch')
    if len({r['source_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate source IDs')
    for i, (row, event) in enumerate(zip(rows, events), 1):
        FinancialEvent.model_validate({k: v for k, v in event.items() if k != 'event_id'})
        if event['event_id'] != f'{name}_EVENT_{i:04d}' or event['source_id'] != row['source_id']:
            raise ValueError('invalid event ID/source order')
        if any(event[f] != row[f] for f in ('date', 'amount', 'direction')):
            raise ValueError('event changed source facts')


def reconcile(bank, document):
    validate_inputs(bank, document)
    rows, events = document['source_rows'], document['events']
    # Source order breaks ties on a day; never assume spreadsheet row equals bank serial.
    bank_order = sorted(range(len(bank)), key=lambda i: (bank[i]['transaction_date'], i))
    event_order = sorted(range(len(events)), key=lambda i: (rows[i]['date'], i))
    brank = {i: r for r, i in enumerate(bank_order)}
    erank = {i: r for r, i in enumerate(event_order)}
    candidates = {}
    for ei, row in enumerate(rows):
        choices = {}
        for bi, transaction in enumerate(bank):
            if (row['date'], row['amount'], row['direction']) != (
                transaction['transaction_date'], transaction['amount'], transaction['direction']):
                continue
            ref, bank_ref = row.get('reference'), transaction['reference']
            reference_conflict = bool(ref and bank_ref and ref != bank_ref)
            reference_match = bool(ref and bank_ref and ref == bank_ref)
            narration_match = bool(narration(row.get('narration')) and
                                   narration(row.get('narration')) == narration(transaction['raw_description']))
            precision = reference_precision(ref, bank_ref) if reference_conflict else None
            precision_supported = bool(precision and narration_match)
            names = [events[ei].get(field) for field in ('merchant', 'payee')]
            supported_names = sorted({name for name in names if name and
                narration(name).casefold() in narration(transaction['raw_description']).casefold() and
                narration(name).casefold() in narration(row.get('narration')).casefold()})
            choices[bi] = {'date': True, 'amount': True, 'direction': True,
                           'reference': reference_match, 'reference_conflict': reference_conflict,
                           'reference_precision': precision,
                           'precision_resolution_supported': precision_supported,
                           'supported_merchant_payee_names': supported_names,
                           'narration_ignoring_whitespace': narration_match}
        candidates[ei] = choices
    matches = {}
    # Use mutually unique strong anchors, then constrain remaining candidates to their order.
    # Equal-looking repeated transactions stay ambiguous unless anchors distinguish them.
    while True:
        available = {}
        for ei, choices in candidates.items():
            if ei in matches:
                continue
            available[ei] = [bi for bi, evidence in choices.items()
                if bi not in matches.values()
                and (not evidence['reference_conflict'] or evidence['precision_resolution_supported'])
                and (evidence['reference'] or evidence['narration_ignoring_whitespace'])
                and all((erank[ei] - erank[ae]) * (brank[bi] - brank[ab]) > 0
                        for ae, ab in matches.items())]
        owners = Counter(bi for choices in available.values() for bi in choices)
        proposals = {ei: choices[0] for ei, choices in available.items()
                     if len(choices) == 1 and owners[choices[0]] == 1}
        accepted = {ei: bi for ei, bi in proposals.items()
                    if all(ei == other or (erank[ei] - erank[other]) * (brank[bi] - brank[ob]) > 0
                           for other, ob in proposals.items())}
        if not accepted:
            break
        matches.update(accepted)
    reverse = {bi: ei for ei, bi in matches.items()}
    decisions = []
    for ei, event in enumerate(events):
        status = 'matched' if ei in matches else ('ambiguous' if candidates[ei] else 'unmatched')
        decisions.append({'event_id': event['event_id'], 'source_id': event['source_id'],
            'status': status, 'transaction_id': bank[matches[ei]]['transaction_id'] if ei in matches else None,
            'reason': ('unique_strong_match_reference_precision_resolved'
                       if candidates[ei][matches[ei]]['precision_resolution_supported']
                       else 'unique_strong_sequence_consistent') if status == 'matched' else
                      ('reference_conflict' if any(e['reference_conflict'] for e in candidates[ei].values()) else
                       'insufficient_identity_or_sequence_conflict' if status == 'ambiguous' else 'no_compatible_bank_facts'),
            'candidates': [{'transaction_id': bank[bi]['transaction_id'], 'evidence': evidence}
                           for bi, evidence in candidates[ei].items()]})
    transactions = []
    for bi, transaction in enumerate(bank):
        ei = reverse.get(bi)
        pending = [d['event_id'] for d in decisions if d['status'] == 'ambiguous'
                   and any(c['transaction_id'] == transaction['transaction_id'] for c in d['candidates'])]
        status = 'matched' if ei is not None else ('ambiguous' if pending else 'unmatched')
        event = events[ei] if ei is not None else None
        transactions.append({'bank_transaction': deepcopy(transaction), 'match_status': status,
            'event': deepcopy(event), 'category': event['category'] if event else None,
            'meaning_needs_review': bool(event and (event['confidence'] == 'low' or event['ambiguities'])),
            'matching_evidence': dict(candidates[ei][bi], chronological_sequence=True,
                                     source_id=event['source_id']) if event else None,
            'candidate_event_ids': pending})
    counts = Counter(t['match_status'] for t in transactions)
    event_counts = Counter(d['status'] for d in decisions)
    result = {'schema_version': '3A.2', 'bank': document['bank'],
            'input_fingerprints': {'bank': digest(bank), 'events': digest(document)},
            'event_source_document': deepcopy(document),
            'transactions': transactions, 'event_decisions': decisions,
            'report': {'bank_transactions': len(bank), 'events': len(events),
                       'matched': counts['matched'], 'unmatched_bank': counts['unmatched'],
                       'ambiguous_bank': counts['ambiguous'],
                       'unmatched_events': event_counts['unmatched'],
                       'ambiguous_events': event_counts['ambiguous'],
                       'meaning_review': sum(t['meaning_needs_review'] for t in transactions)}}
    return validate_schema(ReconciliationOutput, result, 'reconciliation output')


def validate_result(result, bank, document):
    validate_schema(ReconciliationOutput, result, "reconciliation output")
    # Rebuild from authoritative inputs: validates decisions, evidence, counts and enrichment.
    if result != reconcile(bank, document):
        raise ValueError('reconciliation differs from deterministic validated result')
    if [t['bank_transaction'] for t in result['transactions']] != bank:
        raise ValueError('bank rows changed or lost')
    ids = [t['event']['event_id'] for t in result['transactions'] if t['event']]
    if len(ids) != len(set(ids)):
        raise ValueError('event attached more than once')


def run(bank_path, event_path, destination):
    bank_bytes, event_bytes = Path(bank_path).read_bytes(), Path(event_path).read_bytes()
    bank = parse_document(bank_bytes, BankTransactions, str(bank_path))
    document = parse_document(event_bytes, EventDocument, str(event_path))
    result = reconcile(bank, document)
    validate_result(result, bank, document)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    validate_result(json.loads(serialized), bank, document)
    if Path(bank_path).read_bytes() != bank_bytes or Path(event_path).read_bytes() != event_bytes:
        raise ValueError('inputs changed during reconciliation')
    destination = Path(destination)
    if destination.resolve() in (Path(bank_path).resolve(), Path(event_path).resolve()):
        raise ValueError('output cannot overwrite input')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.tmp')
    try:
        temporary.write_text(serialized, encoding='utf-8')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return result['report']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank', choices=['kotak', 'sbi', 'all'], default='all')
    args = parser.parse_args()
    for name in ('kotak', 'sbi') if args.bank == 'all' else (args.bank,):
        report = run(ROOT / f'output/bank/{name}_transactions.json',
                     ROOT / f'output/events/{name}_events.json',
                     ROOT / f'output/reconciled/{name}_reconciled.json')
        print(name, json.dumps(report))


if __name__ == '__main__':
    main()
