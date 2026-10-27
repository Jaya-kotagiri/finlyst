"""Synthetic settlement regressions; no private bank data or API requests."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from tests.support import (
    temporary_directory, doc, share, with_semantics, prepared, settle, origin, receipt, single,
)
from src.relationships import flatten
from src.settlements import build, run, validate


class SettlementTests(unittest.TestCase):
    def test_exact_cross_account_reimbursement_closes_and_preserves_inputs(self):
        ds = [doc('KOTAK', [origin()]), doc('SBI', [receipt(note='Alice sent her share')])]
        data = prepared(ds); before = deepcopy(data); result = build(*data)
        row = single(result)
        self.assertEqual((row['status'], row['verified_repayments'], row['outstanding_amount']), ('CLOSED', '100.00', '0.00'))
        self.assertEqual(result['allocations'][0]['rule'], 'confirmed_3B_reimbursement')
        self.assertEqual(result['classification_effects'][0]['transaction_id'], 'SBI_0001')
        self.assertEqual(data, before)

    def test_no_match_is_not_verified_zero_or_open(self):
        r = settle([doc('KOTAK', [origin()])]); row = single(r)
        self.assertEqual(row['status'], 'UNASSESSED')
        self.assertIsNone(row['outstanding_amount'])
        self.assertFalse(row['verified_zero_repayments'])
        self.assertEqual(r['report']['outstanding_INR']['receivable']['unassessed_known_principal'], '100.00')

    def test_explicit_dated_zero_supports_open(self):
        r = settle([doc('KOTAK', [origin(follow_up='no repayments received as of 2026-08-10')])])
        row = single(r)
        self.assertEqual((row['status'], row['outstanding_amount']), ('OPEN', '100.00'))
        self.assertTrue(row['verified_zero_repayments'])

    def test_partial_with_dated_balance_and_multiple_repayments(self):
        specs = [origin(), receipt('30.00', 'Alice repaid part for KOTAK_0001'),
                 receipt('40.00', 'Alice repaid part for KOTAK_0001; as of 2026-08-03, outstanding balance is 30', '2026-08-03')]
        r = settle([doc('KOTAK', specs)]); row = single(r)
        self.assertEqual((row['status'], row['verified_repayments'], row['outstanding_amount']), ('PARTIAL', '70.00', '30.00'))
        self.assertEqual(len(row['allocation_ids']), 2)

    def test_partial_without_historical_coverage_stays_unassessed(self):
        r = settle([doc('KOTAK', [origin(), receipt('30.00', 'Alice repaid part for KOTAK_0001')])])
        row = single(r)
        self.assertEqual(row['status'], 'UNASSESSED')
        self.assertEqual(row['unadjusted_residual'], '70.00')
        self.assertIsNone(row['outstanding_amount'])

    def test_multiple_partial_receipts_can_exactly_close(self):
        r = settle([doc('KOTAK', [origin(), receipt('30.00', 'Alice repaid part for KOTAK_0001'),
                                 receipt('70.00', 'Alice repaid part for KOTAK_0001', '2026-08-03')])])
        self.assertEqual(single(r)['status'], 'CLOSED')
        self.assertEqual(single(r)['adjustment'], '0.00')

    def test_inclusive_tolerance_and_decimal_example(self):
        for principal, paid, adjustment, status in [('100.00', '95.00', '5.00', 'CLOSED'),
            ('131.50', '131.00', '0.50', 'CLOSED'), ('100.00', '94.99', '0.00', 'UNASSESSED')]:
            with self.subTest(principal=principal, paid=paid):
                r = settle([doc('KOTAK', [origin(principal), receipt(paid)])]); row = single(r)
                self.assertEqual((row['status'], row['adjustment']), (status, adjustment))
                self.assertEqual(row['verified_repayments'], paid)
                self.assertEqual(row['original_principal'], principal)
                if status == 'CLOSED': self.assertEqual(r['clarification_candidates'], [])

    def test_no_tolerance_without_full_intent_or_when_still_owed(self):
        for note in ('Alice repaid part for KOTAK_0001',
                     'Alice repaid in full for KOTAK_0001 but 5 still owed',
                     'Alice repaid for KOTAK_0001; as of 2026-08-02, outstanding balance is 5'):
            r = settle([doc('KOTAK', [origin(), receipt('95.00', note)])]); row = single(r)
            self.assertEqual(row['adjustment'], '0.00')
            self.assertNotEqual(row['status'], 'CLOSED')

    def test_tolerance_applies_once_to_final_residual_not_installments(self):
        specs = [origin('200.00'), receipt('95.00'), receipt('95.00', date='2026-08-03')]
        row = single(settle([doc('KOTAK', specs)]))
        self.assertEqual((row['verified_repayments'], row['unadjusted_residual'], row['adjustment']), ('190.00', '10.00', '0.00'))
        self.assertEqual(row['status'], 'UNASSESSED')
        specs[-1] = receipt('100.00', date='2026-08-03')
        row = single(settle([doc('KOTAK', specs)]))
        self.assertEqual((row['status'], row['adjustment']), ('CLOSED', '5.00'))

    def test_earlier_full_intent_cannot_close_later_partial_payment(self):
        specs = [origin(), receipt('50.00'), receipt('45.00', 'Alice repaid part for KOTAK_0001', '2026-08-03')]
        row = single(settle([doc('KOTAK', specs)]))
        self.assertEqual(row['status'], 'UNASSESSED')
        self.assertEqual(row['adjustment'], '0.00')

    def test_cross_bank_same_day_does_not_invent_final_payment_order(self):
        ds = [doc('KOTAK', [origin(), receipt('45.00', 'Alice repaid part for KOTAK_0001')]),
              doc('SBI', [receipt('50.00')])]
        row = single(settle(ds))
        self.assertEqual(row['status'], 'UNASSESSED')
        self.assertEqual(row['adjustment'], '0.00')

    def test_unexplained_overpayment_is_not_adjusted(self):
        r = settle([doc('KOTAK', [origin(), receipt('103.00')])]); row = single(r)
        self.assertEqual((row['status'], row['verified_repayments'], row['adjustment']), ('UNASSESSED', '103.00', '0.00'))
        self.assertEqual(row['reason'], 'unexplained_overpayment')
        self.assertIsNone(row['outstanding_amount'])
        self.assertTrue(r['clarification_candidates'])
        self.assertIsNone(r['classification_effects'][0]['repayment_is_income_or_expense'])
        self.assertTrue(r['classification_effects'][0]['excess_treatment_requires_review'])

    def test_explicit_minor_excess_is_separate_signed_adjustment(self):
        r = settle([doc('KOTAK', [origin(), receipt('103.00', 'Alice repaid in full for KOTAK_0001, extra 3 for rounding')])])
        row = single(r)
        self.assertEqual((row['status'], row['verified_repayments'], row['adjustment']), ('CLOSED', '103.00', '-3.00'))

    def test_unknown_principal_keeps_verified_receipt_but_not_balance(self):
        spec = dict(amount='100.00', note='Alice share; she will send', components=[share('Alice', None)])
        r = settle([doc('KOTAK', [spec, receipt('40.00')])]); row = single(r)
        self.assertEqual(row['verified_repayments'], '40.00')
        self.assertEqual(row['status'], 'UNASSESSED')
        self.assertIsNone(row['outstanding_amount'])
        self.assertIsNone(row['original_principal'])
        self.assertTrue(row['existing_clarification_ids'])
        self.assertEqual(r['clarification_candidates'], [])

    def test_same_amount_unrelated_or_planned_note_never_matches(self):
        for note in ('Alice sent a gift', 'Alice will repay KOTAK_0001', 'Unrelated payment'):
            r = settle([doc('KOTAK', [origin(), receipt(note=note)])])
            self.assertEqual(r['allocations'], [])

    def test_two_candidate_origins_not_resolved_by_amount(self):
        d = with_semantics(doc('KOTAK', [dict(note='I lent Alice money', amount='100.00'),
                    dict(note='I lent Alice money', amount='200.00'), receipt('100.00', 'Alice repaid the loan')]),
                    [dict(payee='Alice'), dict(payee='Alice')])
        r = settle([d]); self.assertEqual(r['allocations'], [])
        self.assertEqual(len(r['unresolved_candidates']), 1)
        self.assertEqual(len(r['unresolved_candidates'][0]['obligation_ids']), 2)

    def test_explicit_split_multiple_receipts_across_two_obligations(self):
        specs = [origin('40.00'), origin('60.00'),
                 receipt('50.00', 'Alice repaid KOTAK_0001: 20; KOTAK_0002: 30'),
                 receipt('50.00', 'Alice repaid KOTAK_0001: 20; KOTAK_0002: 30', '2026-08-03')]
        r = settle([doc('KOTAK', specs)])
        self.assertEqual(r['report']['by_status']['CLOSED'], 2)
        self.assertEqual(r['report']['verified_repayments'], '100.00')
        self.assertEqual(len(r['allocations']), 4)
        self.assertTrue(all(a['explicit_split'] for a in r['allocations']))

    def test_overallocated_or_duplicate_split_is_rejected_whole(self):
        for note in ('Alice repaid KOTAK_0001: 40; KOTAK_0002: 60',
                     'Alice repaid KOTAK_0001: 20; KOTAK_0001: 20',
                     'Alice repaid KOTAK_0001: -20', 'Alice repaid KOTAK_0001: 20.555'):
            r = settle([doc('KOTAK', [origin('40.00'), origin('60.00'), receipt('50.00', note)])])
            self.assertEqual(r['allocations'], [])
            self.assertEqual(len(r['unresolved_candidates']), 1)

    def test_payable_repayment_requires_debit(self):
        d = with_semantics(doc('SBI', [dict(direction='credit', amount='100.00', note='I borrowed from Alice'),
                 dict(direction='debit', amount='100.00', date='2026-08-02', note='I repaid Alice in full for SBI_0001'),
                 receipt(note='Alice repaid SBI_0001')]), [dict(payer='Alice')])
        r = settle([d]); row = single(r)
        self.assertEqual((row['direction'], row['status'], row['verified_repayments']), ('payable', 'CLOSED', '100.00'))
        self.assertEqual(row['repayment_transaction_ids'], ['SBI_0002'])

    def test_immediate_advance_return_requires_unique_bank_identity(self):
        base = dict(amount='3000.00', note='Dad will send it back right away, he needed it temporarily.',
                    narration='UPI/DR/123456789012/Dad/ICIC/dad@bank/UPI')
        credit = dict(direction='credit', amount='3000.00', narration='UPI/CR/223456789012/Dad/ICIC/dad@bank/UPI')
        def docs(rows): return [with_semantics(doc('SBI', rows), [dict(payee='Dad')])]
        r = settle(docs([base, credit])); self.assertEqual(single(r)['status'], 'CLOSED')
        self.assertEqual(r['allocations'][0]['rule'], 'unique_immediate_bank_counterparty_return')
        for rows in ([base, credit, credit], [base, dict(credit, narration='UPI/CR/223456789012/Other/ICIC/other@bank/UPI')],
                     [base, dict(credit, date='2026-08-03')]):
            r = settle(docs(rows)); self.assertEqual(r['allocations'], [])

    def test_salary_cash_self_transfers_and_plans_are_excluded(self):
        ds = [doc('KOTAK', [origin(), receipt(note='My salary received; Alice repaid KOTAK_0001'),
                            receipt(note='Alice will repay KOTAK_0001; should move this money'),
                            dict(direction='credit', amount='100.00', date='2026-08-02',
                                 narration='UPI/Owner/SBIN/123456789012/spending', note='Alice repaid KOTAK_0001')]),
              doc('SBI', [dict(amount='100.00', date='2026-08-02', narration='UPI/DR/123456789012/Owner/KKBK/self/spending'),
                          dict(narration='ATM WDL ATM CASH', note='Alice repaid KOTAK_0001')])]
        self.assertEqual(settle(ds)['allocations'], [])

    def test_old_or_conflicting_balance_statement_cannot_claim_open_or_closed(self):
        ds = [doc('KOTAK', [origin(follow_up='no repayments received as of 2026-08-01'),
                           receipt('100.00', 'Alice repaid in full for KOTAK_0001')])]
        self.assertEqual(single(settle(ds))['status'], 'CLOSED')  # Older assertion does not contradict later receipt.
        ds = [doc('KOTAK', [origin(follow_up='no repayments received as of 2026-08-02'), receipt()])]
        self.assertEqual(single(settle(ds))['status'], 'UNASSESSED')
        ds = [doc('KOTAK', [origin(follow_up='no repayments received as of 2026-08-01'), dict(date='2026-08-03')])]
        self.assertEqual(single(settle(ds))['status'], 'UNASSESSED')

    def test_validation_rejects_tampering_duplicate_allocation_and_stale_origins(self):
        data = prepared([doc('KOTAK', [origin(), receipt()])]); result = build(*data)
        for mutate in (lambda r: r['allocations'].append(deepcopy(r['allocations'][0])),
                       lambda r: r['allocations'][0].update(amount='101.00'),
                       lambda r: r['obligation_settlement_view'][0].update(outstanding_amount='20.00')):
            bad = deepcopy(result); mutate(bad)
            with self.assertRaises(ValueError): validate(bad, data[4], flatten(data[0]), data[1], data[2])
        bad = deepcopy(data); bad[4]['obligations'][0]['original_amount'] = '101.00'
        with self.assertRaises(ValueError): build(*bad)

    def test_idempotent_file_run_and_preservation(self):
        data = prepared([doc('KOTAK', [origin()]), doc('SBI', [receipt()])])
        with temporary_directory() as tmp:
            root = Path(tmp)
            files = {f'output/reconciled/{d["bank"].lower()}_reconciled.json': d for d in data[0]}
            files.update({'output/relationships/relationships.json': data[1],
                'output/classification/effective_transactions.json': data[2], 'output/audit/coverage_audit.json': data[3],
                'output/obligations/obligations.json': dict(data[4], verification={'protected_file_sha256': {}, 'source_files_unchanged': True})})
            for name, value in files.items():
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(value), encoding='utf-8')
            before = {p: p.read_bytes() for p in root.rglob('*.json')}
            first = run(root); output = root / 'output/settlements/settlements.json'; encoded = output.read_bytes()
            self.assertEqual(run(root), first); self.assertEqual(output.read_bytes(), encoded)
            self.assertTrue(all(p.read_bytes() == b for p, b in before.items()))
            self.assertEqual(build(*data), build(*prepared(list(reversed(data[0])))))
