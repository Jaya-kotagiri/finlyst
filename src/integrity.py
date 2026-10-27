"""3C.4: read-only cross-layer financial integrity and migration readiness audit."""

from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .audit import inspect_workbook, coverage_counts
from .classification import build as classify, SEMANTIC_FIELDS
from .kotak import parse_kotak
from .sbi import parse_sbi
from .notes import read_notes
from .obligations import build as build_origins
from .output_models import (BankTransactions, ClassificationOutput, CoverageOutput, EventDocument,
    ReconciledDocuments, ReconciliationOutput, RelationshipsOutput, ObligationsOutput,
    ObligationsFileOutput, SettlementsOutput, SettlementsFileOutput)
from .schema_validation import parse_document, validate_schema
from .reconcile import digest, reconcile, validate_inputs
from .relationships import derive as relate, flatten, upi_reference
from .settlements import build as settle, chronological
from .validation import summary

ROOT = Path(__file__).resolve().parents[1]
ZERO = Decimal('0.00')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decimal(value):
    require(isinstance(value, str) and re.fullmatch(r'-?\d+\.\d{2}', value), 'money must be an exact two-decimal string')
    return Decimal(value)


def without_verification(document):
    return {k: v for k, v in document.items() if k != 'verification'}


def independent_ledger(origins, settlements, records, classification):
    """Accounting and reference checks independent of settlement decision replay."""
    claims = {o['obligation_id']: o for o in origins['obligations']}
    allocations = settlements['allocations']
    require(len({a['allocation_id'] for a in allocations}) == len(allocations), 'duplicate allocation ID')
    require(len({(a['obligation_id'], a['repayment_transaction_id']) for a in allocations}) == len(allocations), 'duplicate repayment/obligation pair')
    views = settlements['obligation_settlement_view']
    require(len(views) == len(claims) and {v['obligation_id'] for v in views} == set(claims), 'obligation view coverage changed')
    effective = {r['transaction_id']: r['effective'] for r in classification['transactions']}
    used, by_claim = defaultdict(lambda: ZERO), defaultdict(list)
    for a in allocations:
        identifier = a['repayment_transaction_id']; record = records[identifier]; claim = claims[a['obligation_id']]
        amount = decimal(a['amount'])
        require(amount > ZERO, 'nonpositive allocation')
        require(a['allocation_id'] == 'ALLOC_' + digest([identifier, a['obligation_id']])[:20], 'unstable allocation ID')
        require(record['bank']['direction'] == ('credit' if claim['direction'] == 'receivable' else 'debit'), 'wrong repayment direction')
        require(all(chronological(records[i], record) for i in claim['originating_transaction_ids']), 'repayment predates origin')
        require(effective[identifier]['treatment'] not in ('self_transfer', 'cash_conversion', 'salary_income', 'expense'), 'excluded flow used as repayment')
        require(a['evidence']['bank_transaction_sha256'] == digest(record['bank']) and
                a['evidence']['source_row_sha256'] == digest(record['source']), 'repayment provenance mismatch')
        require(a['evidence_strength'] == 'high', 'verified allocation lacks strong evidence')
        require(a['confirmation_method'] in ('inferred', 'source_supported', 'explicit_source_allocation'), 'invalid confirmation method')
        if a['rule'] == 'unique_immediate_bank_counterparty_return':
            require(a['confirmation_method'] == 'inferred', 'bank inference mislabeled as explicit confirmation')
        used[identifier] += amount; by_claim[a['obligation_id']].append(a)
    for identifier, amount in used.items():
        require(amount <= decimal(records[identifier]['bank']['amount']), 'repayment overallocated or reused')
    confirmed = {'receivable': ZERO, 'payable': ZERO}
    for row in views:
        oid = row['obligation_id']; claim = claims[oid]; payments = by_claim[oid]
        actual = sum((decimal(a['amount']) for a in payments), ZERO)
        require(row['origin_sha256'] == digest(claim) and row['original_principal'] == claim['original_amount'], 'origin/principal changed')
        require(row['direction'] == claim['direction'], 'obligation direction changed')
        require(decimal(row['verified_repayments']) == actual, 'repayment total does not equal allocations')
        require(row['allocation_ids'] == sorted(a['allocation_id'] for a in payments), 'allocation provenance coverage changed')
        require(row['repayment_transaction_ids'] == sorted({a['repayment_transaction_id'] for a in payments}), 'repayment ID coverage changed')
        require(row['confirmation_methods'] == sorted({a['confirmation_method'] for a in payments}), 'confirmation method lost')
        require(row['status'] in ('OPEN', 'PARTIAL', 'CLOSED', 'UNASSESSED'), 'invalid lifecycle status')
        adjustment = decimal(row['adjustment'])
        require(abs(adjustment) <= Decimal('5.00'), 'adjustment exceeds final residual tolerance')
        if claim['original_amount'] is None:
            require(row['status'] == 'UNASSESSED' and row['unadjusted_residual'] is None, 'unknown principal forced into settlement')
        else:
            residual = decimal(claim['original_amount']) - actual
            require(decimal(row['unadjusted_residual']) == residual, 'incorrect arithmetic residual')
            if row['status'] == 'CLOSED':
                require(payments and decimal(row['outstanding_amount']) == ZERO and residual == adjustment, 'unsupported closure arithmetic')
            if row['outstanding_amount'] is not None:
                require(decimal(row['outstanding_amount']) == residual - adjustment >= ZERO, 'invalid outstanding amount')
        if row['status'] == 'UNASSESSED':
            require(row['outstanding_amount'] is None and not row['verified_zero_repayments'], 'unassessed confused with verified zero')
        else:
            require(row['outstanding_amount'] is not None, 'assessed status missing balance')
            confirmed[claim['direction']] += decimal(row['outstanding_amount'])
        if row['status'] in ('OPEN', 'PARTIAL'):
            require(row['balance_assertion'] is not None, 'open/partial assumed from missing history')
        if adjustment:
            evidence = row['adjustment_evidence']
            require(row['status'] == 'CLOSED' and evidence, 'adjustment not explained')
            supporting = [a for a in payments if a['allocation_id'] in evidence['allocation_ids']]
            require(supporting and all(a['full_settlement_intent'] for a in supporting), 'minor adjustment lacks full-settlement intent')
            last_day = max(records[a['repayment_transaction_id']]['bank']['transaction_date'] for a in payments)
            require(all(records[a['repayment_transaction_id']]['bank']['transaction_date'] == last_day for a in supporting), 'tolerance applied before final payment')
            require(not row['balance_assertion'] or row['balance_assertion'].get('amount') in (None, '0.00'), 'known positive debt written off')
            if adjustment < ZERO:
                require(all(a['explicit_minor_overpayment'] for a in supporting), 'unexplained overpayment adjusted')
    report = settlements['report']
    require(decimal(report['verified_repayments']) == sum(used.values(), ZERO), 'verified-repayment summary double counts')
    for direction, value in confirmed.items():
        require(decimal(report['outstanding_INR'][direction]['confirmed_outstanding']) == value, 'confirmed and unresolved balances mixed')
    effects = settlements['classification_effects']
    require(len(effects) == len(used) and {e['transaction_id'] for e in effects} == set(used), 'repayment classification overlay coverage mismatch')
    for effect in effects:
        identifier = effect['transaction_id']
        require(decimal(effect['verified_principal_repayment_amount']) == used[identifier], 'overlay amount mismatch')
        require(decimal(effect['remaining_transaction_amount_unclassified_by_this_stage']) + used[identifier] == decimal(records[identifier]['bank']['amount']), 'overlay adds a cashflow')
        require(effect['original_effective_treatment'] == effective[identifier]['treatment'], 'overlay lost original treatment')
    return {'allocations': len(allocations), 'unique_repayment_transactions': len(used),
            'obligations': len(claims), 'verified_repayments': format(sum(used.values(), ZERO), '.2f')}


def audit_layers(banks, events, documents, relationships, classification, coverage, origins, settlements, current_dataset=False):
    checks = []
    def check(name, operation):
        try:
            detail = operation()
            checks.append({'check': name, 'status': 'PASS', 'details': detail})
        except (ValueError, KeyError, TypeError, IndexError, ArithmeticError) as exc:
            checks.append({'check': name, 'status': 'FAIL', 'error': str(exc)})
    bank_rows = [t for bank in sorted(banks) for t in banks[bank]]
    bank_ids = [t['transaction_id'] for t in bank_rows]
    bank_map = {t['transaction_id']: t for t in bank_rows}

    def bank_check():
        require(len(bank_ids) == len(set(bank_ids)), 'bank IDs are not unique')
        totals = {bank: summary(rows) for bank, rows in sorted(banks.items())}
        require(not any(r['duplicate_pairs'] for r in totals.values()), 'duplicate normalized bank facts require review')
        if current_dataset: require(len(bank_rows) == 154, 'expected 154 bank transactions')
        return totals
    check('bank_schema_ids_and_duplicate_detection', bank_check)

    def balances():
        compared = 0
        for rows in banks.values():
            for previous, row in zip(rows, rows[1:]):
                if previous['balance_after'] is None or row['balance_after'] is None: continue
                movement = decimal(row['amount']) * (1 if row['direction'] == 'credit' else -1)
                require(decimal(previous['balance_after']) + movement == decimal(row['balance_after']), 'bank balance discontinuity: ' + row['transaction_id'])
                compared += 1
        return {'adjacent_balances_checked': compared, 'first_opening_balance_not_independently_asserted': True}
    check('exact_bank_balance_continuity', balances)

    def semantics():
        ids = []
        for bank, event_doc in events.items():
            validate_inputs(banks[bank], event_doc)
            ids.extend(e['event_id'] for e in event_doc['events'])
        require(len(ids) == len(set(ids)), 'duplicate semantic IDs')
        if current_dataset: require(len(ids) == 85, 'expected 85 semantic events')
        return {'events_preserved': len(ids)}
    check('stage_2_schema_and_source_facts', semantics)

    def reconciliation():
        validate_schema(ReconciledDocuments, documents, "integrity documents input")
        seen, linked = [], []
        for d in documents:
            bank = d['bank'].lower()
            require(d == reconcile(banks[bank], events[bank]), '3A differs from source replay')
            for row in d['transactions']:
                seen.append(row['bank_transaction']['transaction_id'])
                if row['event']: linked.append(row['event'])
        require(Counter(seen) == Counter(bank_ids), 'bank coverage changed in 3A')
        original = {e['event_id']: e for d in events.values() for e in d['events']}
        require(len(linked) == len(original) and {e['event_id']: e for e in linked} == original, 'semantic content lost or duplicated')
        return {'bank_transactions': len(seen), 'matched_events': len(linked), 'without_events': len(seen) - len(linked)}
    check('reconciliation_coverage_and_full_meanings', reconciliation)

    def relationship_check():
        validate_schema(RelationshipsOutput, relationships, "integrity relationships input")
        require(relate(documents) == relationships, 'relationships differ from source-supported derivation')
        seen = set(); transfers = 0
        for rel in relationships['relationships']:
            key = (rel['type'], tuple(sorted(rel['transaction_ids'])), rel['rule'])
            require(key not in seen, 'duplicate financial relationship'); seen.add(key)
            if rel['type'] == 'self_transfer' and rel['status'] == 'confirmed':
                a, b = [bank_map[i] for i in rel['transaction_ids']]
                require(a['bank'] != b['bank'] and a['direction'] != b['direction'] and a['amount'] == b['amount'], 'unbalanced self-transfer')
                require(upi_reference(a['raw_description']) and upi_reference(a['raw_description']) == upi_reference(b['raw_description']), 'invented transfer without shared bank identifier')
                transfers += 1
        return {'relationships': len(seen), 'confirmed_transfer_pairs': transfers}
    check('relationship_support_and_uniqueness', relationship_check)

    def classification_check():
        validate_schema(ClassificationOutput, classification, "integrity classification input")
        require(classify(documents, relationships) == classification, 'classification replay mismatch')
        records = flatten(documents)
        for row in classification['transactions']:
            original = records[row['transaction_id']]; effective = row['effective']
            require({k: row[k] for k in original['bank']} == original['bank'], 'authoritative facts changed in classification')
            event = original['event']
            require(row['semantic'] == ({k: event[k] for k in SEMANTIC_FIELDS} if event else None), 'ordinary meaning lost')
            if effective['treatment'] in ('self_transfer', 'cash_conversion', 'loan_advance', 'loan_borrowing', 'reimbursement_received'):
                require(effective['income'] is False and effective['expense'] is False, 'asset/principal movement counted as income or expense')
            if effective['treatment'] == 'unknown':
                require(effective['income'] is None and effective['expense'] is None, 'unknown forced into financial totals')
        return classification['report']
    check('classification_semantics_and_counting', classification_check)

    def coverage_check():
        validate_schema(CoverageOutput, coverage, "integrity coverage input")
        rows = coverage['transactions']
        require(len(rows) == len(bank_rows) and {r['transaction_id'] for r in rows} == set(bank_ids), 'coverage audit missing bank rows')
        records = flatten(documents)
        for row in rows:
            record = records[row['transaction_id']]
            require(all(row[k] == v for k, v in record['bank'].items()), 'coverage audit bank facts changed')
            require(row['event_id'] == (record['event']['event_id'] if record['event'] else None), 'coverage audit lost event')
        if 'summary' in coverage:
            require(coverage['summary'] == coverage_counts(rows), 'coverage audit counts inconsistent')
        return {'missing_semantics_are_not_failed_extraction': True,
                'without_events': sum(not r['event'] for r in records.values())}
    check('notes_coverage_vs_processing_failure', coverage_check)

    def obligation_check():
        validate_schema(ObligationsOutput, origins, "integrity origins input")
        require(build_origins(documents, relationships, classification, coverage) == without_verification(origins), 'obligation origin replay mismatch')
        keys = [(tuple(o['originating_transaction_ids']), o['direction'], o['counterparty']) for o in origins['obligations']]
        require(len(keys) == len(set(keys)), 'duplicate origin/party claim')
        return origins['report']
    check('obligation_origin_amounts_and_provenance', obligation_check)

    def settlement_check():
        validate_schema(SettlementsOutput, settlements, "integrity settlements input")
        require(settle(documents, relationships, classification, coverage, origins) == without_verification(settlements), 'settlement source/evidence replay mismatch')
        return settlements['report']
    check('settlement_decisions_and_provenance', settlement_check)
    check('independent_allocation_and_decimal_ledger', lambda: independent_ledger(origins, settlements, flatten(documents), classification))

    def deterministic():
        reversed_docs = list(reversed(documents))
        require(relate(reversed_docs) == relationships, 'relationship IDs/order unstable')
        require(classify(reversed_docs, relationships) == classification, 'classification unstable')
        require(build_origins(reversed_docs, relationships, classification, coverage) == without_verification(origins), 'origin IDs/order unstable')
        require(settle(reversed_docs, relationships, classification, coverage, origins) == without_verification(settlements), 'settlement IDs/order unstable')
        return {'reversed_document_order_and_repeated_derivation': True}
    check('stable_ids_and_deterministic_rebuilds', deterministic)

    if current_dataset:
        def current_questions():
            view = {v['obligation_id']: v for v in settlements['obligation_settlement_view']}
            claims = origins['obligations']
            dad, = [o for o in claims if o['originating_transaction_ids'] == ['SBI_0025']]
            dv = view[dad['obligation_id']]
            require(dv['status'] == 'CLOSED' and dv['repayment_transaction_ids'] == ['SBI_0026'], 'Dad repayment link changed')
            require(dv['repayment_evidence_strength'] == 'high' and dv['confirmation_methods'] == ['inferred'], 'Dad evidence method/strength conflated')
            unknown = [o for o in claims if o['original_amount'] is None]
            require(len(unknown) == 3 and all(view[o['obligation_id']]['status'] == 'UNASSESSED' for o in unknown), 'Nandy/group-meal uncertainty lost')
            ticket, = settlements['unresolved_candidates']
            require(ticket['repayment_transaction_id'] == 'SBI_0023' and len(ticket['obligation_ids']) == 2, 'train-ticket allocation forced')
            require(not any(a['repayment_transaction_id'] == 'SBI_0023' for a in settlements['allocations']), 'unresolved train receipt allocated')
            ambiguous = {'KOTAK_0026', 'KOTAK_0027', 'KOTAK_0060', 'SBI_0009'}
            require(ambiguous <= {i for q in origins['clarification_candidates'] for i in q['transaction_ids']}, 'annotated terms clarification lost')
            require(not ambiguous & {i for o in claims for i in o['originating_transaction_ids']}, 'ambiguous terms made into obligations')
            printout = next(t for t in classification['transactions'] if t['transaction_id'] == 'SBI_0001')
            require(printout['amount'] == '78.00' and printout['effective']['treatment'] == 'expense' and printout['semantic']['purpose_context'], 'printout expense meaning lost')
            return {'dad': {'origin': 'SBI_0025', 'repayment': 'SBI_0026', 'status': 'CLOSED', 'evidence_strength': 'high', 'confirmation_method': 'inferred'},
                    'unknown_principals': 3, 'annotated_financial_term_questions': 4, 'train_receipt_allocation': 'UNRESOLVED'}
        check('current_dataset_uncertainty_and_Dad_inference', current_questions)
    return checks


def run(root=ROOT):
    root = Path(root)
    folders = ('input', 'output/bank', 'output/events', 'output/reconciled', 'output/relationships',
               'output/classification', 'output/obligations', 'output/settlements')
    protected = sorted([p for folder in folders for p in (root / folder).rglob('*') if p.is_file()]
                       + [root / 'output/audit/coverage_audit.json'])
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    def read(path, schema): return parse_document((root / path).read_bytes(), schema, str(root / path))
    banks = {b: read(f'output/bank/{b}_transactions.json', BankTransactions) for b in ('kotak', 'sbi')}
    events = {b: read(f'output/events/{b}_events.json', EventDocument) for b in banks}
    documents = [read(f'output/reconciled/{b}_reconciled.json', ReconciliationOutput) for b in banks]
    relationships = read('output/relationships/relationships.json', RelationshipsOutput)
    classification = read('output/classification/effective_transactions.json', ClassificationOutput)
    coverage = read('output/audit/coverage_audit.json', CoverageOutput)
    origins = read('output/obligations/obligations.json', ObligationsFileOutput)
    settlements = read('output/settlements/settlements.json', SettlementsFileOutput)
    checks = audit_layers(banks, events, documents, relationships, classification, coverage, origins, settlements, True)
    try:
        for bank, parser in (('kotak', parse_kotak), ('sbi', parse_sbi)):
            inventory = coverage['sources'][bank]
            require(parser(root / inventory['bank_file']) == banks[bank], 'original bank source differs from normalized output')
            notes = read_notes(root / inventory['notes_file'])
            raw, _ = inspect_workbook(root / inventory['notes_file'])
            require(notes == events[bank]['source_rows'] and set(raw) == {n['source_id'] for n in notes}, 'source note extraction changed')
            for n in notes:
                require((n.get('note') or None) == (raw[n['source_id']]['meaning'] or None), 'source meaning changed')
        checks.append({'check': 'original_bank_and_notes_reinspection', 'status': 'PASS', 'details': 'CSV/XLSX and all 85 extracted rows agree with saved sources.'})
    except (ValueError, KeyError, TypeError) as exc:
        checks.append({'check': 'original_bank_and_notes_reinspection', 'status': 'FAIL', 'error': str(exc)})
    try:
        snapshots = [coverage['verification']['source_sha256'], origins['verification']['protected_file_sha256'], settlements['verification']['source_sha256']]
        for snapshot in snapshots:
            require(all(hashes.get(name) == value for name, value in snapshot.items()), 'upstream source snapshot mismatch')
        require(all(hashlib.sha256(p.read_bytes()).hexdigest() == hashes[str(p.relative_to(root))] for p in protected), 'protected file changed during audit')
        checks.append({'check': 'source_hash_chain_and_read_only_audit', 'status': 'PASS', 'details': {'protected_files': len(protected)}})
    except ValueError as exc:
        checks.append({'check': 'source_hash_chain_and_read_only_audit', 'status': 'FAIL', 'error': str(exc)})
    failed = [c['check'] for c in checks if c['status'] == 'FAIL']
    report = {'schema_version': '3C.4.1', 'result': 'FAIL' if failed else 'PASS',
        'passed_checks': len(checks) - len(failed), 'failed_checks': failed, 'checks': checks,
        'readiness': {'ready_for_postgresql_migration': not failed, 'financial_questions_fully_resolved': False,
                      'conditions': ['Preserve nullable unknown amounts, UNASSESSED and confidence/method separately.',
                                     'Retain original records, source hashes and unique allocation/transaction IDs.',
                                     'Apply settlement classification overlays without adding cashflows.']},
        'unresolved_questions': {'train_ticket_receipt': 'SBI_0023: 3500.00 unallocated across 3538.00 original principal.',
            'unknown_principals': 'Nandy: 131 vs 131.50; group meal: original Manasa/Deena shares not established by 180/100 receipts.',
            'financial_terms': ['KOTAK_0026', 'KOTAK_0027', 'KOTAK_0060', 'SBI_0009'],
            'source_notes': '69 bank rows outside supplied workbooks; all 85 supplied notes rows processed. This is incomplete source coverage, not processing loss.'},
        'corrections': ['Separate repayment evidence strength from confirmation method; Dad remains high/inferred/CLOSED.',
            'A positive dated outstanding-balance follow-up blocks tolerance even if payment narration claims full settlement.',
            'Full-settlement intent for one explicit split does not close other obligation shares.',
            'Only the consumed coverage audit is an upstream fingerprint dependency; downstream audit reports cannot create hash cycles.'],
        'source_sha256': hashes}
    output = root / 'output/audit/financial_integrity.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.json.tmp')
    try:
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'); temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return report


if __name__ == '__main__':
    result = run()
    print(json.dumps({k: result[k] for k in ('result', 'passed_checks', 'failed_checks', 'readiness')}, indent=2))
    if result['failed_checks']: raise SystemExit(1)
