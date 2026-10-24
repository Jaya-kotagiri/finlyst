"""Synthetic tests for conservative relationship detection and accounting integrity."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.reconcile import digest, reconcile
from src.relationships import derive, run, upi_reference, flatten, validate_relationships


def doc(bank, specs):
    transactions, rows, events = [], [], []
    for i, spec in enumerate(specs, 1):
        t = dict(transaction_id=f'{bank}_{i:04d}', bank=bank, account='synthetic',
            transaction_date=spec.get('date', '2026-08-01'), amount=spec.get('amount', '100.00'),
            direction=spec.get('direction', 'debit'), raw_description=spec.get('narration', f'Synthetic {i}'),
            reference=None, balance_after='500.00')
        transactions.append(t)
        if 'note' not in spec:
            continue
        j = len(events) + 1
        row = dict(source_id=f'sheet:{i}', date=t['transaction_date'], amount=t['amount'],
                   direction=t['direction'], narration=t['raw_description'], note=spec['note'])
        if spec.get('follow_up'):
            row['follow_up'] = spec['follow_up']
        rows.append(row)
        events.append(dict(event_id=f'{bank}_EVENT_{j:04d}', source_id=row['source_id'],
            event_type=spec.get('event_type'), date=row['date'], amount=row['amount'], direction=row['direction'],
            currency=None, merchant=None, payee=None, payer=None, category=None, subcategory=None,
            description=None, purpose_context=None, entities=[], components=spec.get('components', []),
            matching_clues=[], confidence='high', ambiguities=[], additional_context=[]))
    return reconcile(transactions, dict(bank=bank, source_rows=rows, events=events, source_rows_sha256=digest(rows)))


def share(party='Alice', amount='40.00'):
    return dict(kind='receivable', amount=amount, party=party, status='planned', description='Share')


def treatment(result, identifier):
    return next(t for t in result['transaction_treatments'] if t['transaction_id'] == identifier)


class RelationshipTests(unittest.TestCase):
    def test_self_transfer_overrides_salary_type_and_preserves_purpose(self):
        docs = [doc('KOTAK', [dict(direction='credit', narration='UPI/Owner/SBIN/123456789012/spending',
                                 note='Transferred salary for monthly spending', event_type='income')]),
                doc('SBI', [dict(narration='WDL UPI/DR/123456789012/Owner/KKBK/self/spending')])]
        before = deepcopy(docs); result = derive(docs)
        self.assertEqual(result['report']['by_type']['self_transfer'], 1)
        for t in result['transaction_treatments']:
            self.assertFalse(t['income']); self.assertFalse(t['expense'])
        self.assertIn('monthly spending', result['relationships'][0]['purpose'])
        self.assertEqual(docs, before)
        self.assertEqual(json.dumps(result), json.dumps(derive(list(reversed(docs)))))

    def test_same_amount_without_shared_id_is_not_self_transfer(self):
        result = derive([doc('KOTAK', [dict(direction='credit')]), doc('SBI', [dict()])])
        self.assertNotIn('self_transfer', result['report']['by_type'])

    def test_duplicate_transfer_candidates_not_forced(self):
        incoming = dict(direction='credit', narration='UPI/Owner/SBIN/123456789012/spending')
        result = derive([doc('KOTAK', [incoming, incoming]),
                         doc('SBI', [dict(narration='UPI/DR/123456789012/Owner/KKBK/self/spending')])])
        self.assertTrue(all(r['status']=='unresolved' for r in result['relationships']))
        self.assertTrue(all(t['income'] is None for t in result['transaction_treatments']))

    def test_cross_bank_nonadjacent_share_reimbursement(self):
        expense = dict(note='Alice share 40, my share 60', components=[share()])
        credit = dict(direction='credit', amount='40.00', date='2026-08-04', note='Alice sent her share')
        result = derive([doc('KOTAK', [expense, dict()]), doc('SBI', [dict(), credit])])
        relationship = next(r for r in result['relationships'] if r['type']=='reimbursement')
        self.assertEqual(relationship['status'], 'confirmed')
        self.assertEqual(relationship['transaction_ids'], ['KOTAK_0001', 'SBI_0002'])
        self.assertFalse(treatment(result,'SBI_0002')['income'])
        self.assertIsNone(treatment(result,'KOTAK_0001')['expense'])

    def test_ai_component_without_raw_support_does_not_link(self):
        expense = dict(note='Food purchase', components=[share()])
        credit = dict(direction='credit', amount='40.00', note='Alice sent her share')
        result = derive([doc('KOTAK', [expense, credit])])
        self.assertEqual(next(r for r in result['relationships'] if r['type']=='reimbursement')['status'],'unresolved')

    def test_two_credits_for_one_share_remain_candidates(self):
        expense = dict(note='Alice share 40', components=[share()])
        credits = [dict(direction='credit', amount='40.00', note=n) for n in ('Alice sent her share', 'Alice repaid her share')]
        result = derive([doc('KOTAK', [expense] + credits)])
        reimbursements = [r for r in result['relationships'] if r['type']=='reimbursement']
        self.assertEqual(len(reimbursements),2)
        self.assertTrue(all(r['status']=='unresolved' for r in reimbursements))

    def test_planned_transfer_does_not_create_execution(self):
        result = derive([doc('SBI',[dict(direction='credit', note='Alice sent her share',
                    follow_up='need to send it to kotak')])])
        planned = next(r for r in result['relationships'] if r['type']=='planned_transfer')
        self.assertEqual(planned['status'],'planned')
        self.assertEqual(planned['details']['execution'],'not_established')
        self.assertNotIn('self_transfer',result['report']['by_type'])

    def test_ai_only_planned_transfer_not_accepted(self):
        component = dict(kind='payable', amount='40.00', party='Kotak account', status='planned',
                         description='Move money to Kotak')
        result = derive([doc('SBI',[dict(direction='credit', amount='40.00',
                    note='Alice sent her share',components=[component])])])
        self.assertNotIn('planned_transfer',result['report']['by_type'])

    def test_financial_integrity_validator_rejects_double_counting(self):
        documents = [doc('SBI',[dict(narration='ATM WDL ATM CASH 12345')])]
        result = derive(documents)
        result['transaction_treatments'][0]['expense'] = True
        with self.assertRaises(ValueError):
            validate_relationships(result, flatten(documents))

    def test_advance_not_repayment_and_actual_return_only_candidate(self):
        specs = [dict(note='Dad will send it back right away, he needed it temporarily.', event_type='loan_repayment',
                      narration='UPI/DR/123456789012/Dad/ICIC/dad@bank/UPI'),
                 dict(direction='credit', narration='UPI/CR/223456789012/Dad/ICIC/dad@bank/UPI')]
        result = derive([doc('SBI',specs)])
        self.assertEqual(treatment(result,'SBI_0001')['classification'],'loan_advance')
        r = next(r for r in result['relationships'] if r['type']=='possible_loan_return')
        self.assertEqual(r['status'],'unresolved')
        self.assertIsNone(treatment(result,'SBI_0002')['income'])
        self.assertNotIn('possible_loan_return',derive([doc('SBI',specs[:1])])['report']['by_type'])

    def test_borrowed_principal_not_income_and_gift_not_loan(self):
        result = derive([doc('SBI',[dict(direction='credit',note='I borrowed from Alice'),
                                   dict(direction='credit',note='Dad gave me a gift')])])
        self.assertEqual(treatment(result,'SBI_0001')['classification'],'loan_borrowing')
        self.assertFalse(treatment(result,'SBI_0001')['income'])
        self.assertEqual(treatment(result,'SBI_0002')['classification'],'unclassified')

    def test_cash_exchange_and_atm_are_holdings_movements(self):
        result = derive([doc('KOTAK',[dict(amount='500.00',note='Alice gave me the 500 in cash because she wanted upi money.'),
                                     dict(narration='ATM WDL ATM CASH 12345')])])
        self.assertEqual(result['report']['by_type']['cash_conversion'],2)
        self.assertEqual({r['status'] for r in result['relationships']},{'confirmed','source_asserted'})
        self.assertTrue(all(t['expense'] is False for t in result['transaction_treatments']))
        self.assertTrue(all(r['details']['not_current_cash_balance'] for r in result['relationships']))

    def test_expected_cash_is_not_received_cash(self):
        result = derive([doc('KOTAK',[dict(note='Alice is supposed to give cash of 40', components=[share()])])])
        self.assertNotIn('cash_conversion',result['report']['by_type'])
        self.assertIn('reimbursement_expected',result['report']['by_type'])

    def test_unmatched_transactions_and_unknown_purpose_remain_unclassified(self):
        result = derive([doc('KOTAK',[dict(amount='5.00'),dict(amount='40.00',note='',event_type='grocery purchase')])])
        self.assertEqual(result['report']['classifications'],{'unclassified':2})

    def test_idempotent_files_and_source_immutability(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tests/fixtures') as folder:
            root = Path(folder); source=root/'reconciled.json'; output=root/'relationships.json'
            source.write_text(json.dumps(doc('KOTAK',[dict(narration='ATM WDL ATM CASH 12345')])))
            original = source.read_bytes(); run([source],output); first=output.read_bytes(); run([source],output)
            self.assertEqual(first,output.read_bytes()); self.assertEqual(original,source.read_bytes())
            with self.assertRaises(ValueError): run([source],source)

    def test_upi_parser_ignores_phone_and_local_reference(self):
        self.assertEqual(upi_reference('UPI/Alice/SBIN/123456789012/payment'),'123456789012')
        self.assertIsNone(upi_reference('contact 123456789012'))


if __name__=='__main__':
    unittest.main()
