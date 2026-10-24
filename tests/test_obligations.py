"""Synthetic obligation origins; no API or settlement engine."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_relationships import doc, share
from test_classification import with_semantics
from src.classification import build as classify
from src.relationships import derive, flatten
from src.obligations import build, run, validate


def inputs(documents):
    relationships = derive(documents)
    classification = classify(documents, relationships)
    audit = {'transactions': [dict(r['bank'], event_id=r['event']['event_id'] if r['event'] else None,
                                   effective_treatment=t['effective']['treatment'])
                              for r, t in zip(flatten(documents).values(), classification['transactions'])]}
    return documents, relationships, classification, audit


def generate(documents):
    return build(*inputs(documents))


class ObligationTests(unittest.TestCase):
    def test_advance_overrides_ai_repayment_and_is_not_outstanding(self):
        d = with_semantics(doc('SBI', [dict(amount='3000.00', note='Dad will send it back right away, he needed it temporarily.',
                                           event_type='loan_repayment')]), [dict(payee='Dad')])
        data = inputs([d]); before = deepcopy(data)
        result = build(*data); o, = result['obligations']
        self.assertEqual((o['direction'], o['counterparty'], o['original_amount']), ('receivable', 'Dad', '3000.00'))
        self.assertEqual(o['settlement_status'], 'UNASSESSED')
        self.assertNotIn('outstanding_amount', o)
        self.assertFalse(result['report']['totals_are_outstanding_balances'])
        self.assertEqual(data, before)

    def test_borrowed_credit_is_payable(self):
        d = with_semantics(doc('SBI', [dict(direction='credit', amount='200.00', note='I borrowed 200 from Alice')]), [dict(payer='Alice')])
        o, = generate([d])['obligations']
        self.assertEqual((o['direction'], o['type'], o['original_amount']), ('payable', 'loan_borrowing', '200.00'))

    def test_explicit_payable_requires_source_not_ai_component(self):
        component = dict(share('Alice', '40.00'), kind='payable')
        d = doc('SBI', [dict(note='I owe Alice 40 for my ticket', components=[component]),
                        dict(note='My ticket', components=[component]),
                        dict(note='I do not owe Alice anything', components=[component]),
                        dict(note='I owe Alice nothing', components=[component])])
        r = generate([d]); o, = r['obligations']
        self.assertEqual(o['direction'], 'payable')
        self.assertEqual(o['original_amount'], '40.00')
        self.assertEqual(r['report']['known_original_principal_INR']['payable'], '40.00')

    def test_cross_bank_receipt_is_context_not_new_origin_or_settlement(self):
        ds = [doc('KOTAK', [dict(note='Alice share 40, my share 60', components=[share()])]),
              doc('SBI', [dict(direction='credit', amount='40.00', date='2026-08-03', note='Alice sent her share',
                               components=[dict(share('Kotak account'), kind='payable')], follow_up='need to send it to kotak')])]
        r = generate(ds); o, = r['obligations']
        self.assertEqual(o['original_amount'], '40.00')
        self.assertEqual(o['originating_transaction_ids'], ['KOTAK_0001'])
        self.assertTrue(any(c['transaction_ids'] == ['KOTAK_0001', 'SBI_0001'] for c in o['existing_relationship_context']))
        self.assertEqual(o['settlement_status'], 'UNASSESSED')

    def test_partial_known_shares_do_not_use_gross(self):
        d = doc('KOTAK', [dict(amount='200.00', note='Alice share 40, Bob will send his share later',
                               components=[share(), share('Bob', None)])])
        r = generate([d]); parties = {o['counterparty']: o for o in r['obligations']}
        self.assertEqual(parties['Alice']['original_amount'], '40.00')
        self.assertIsNone(parties['Bob']['original_amount'])
        self.assertEqual(r['report']['known_original_principal_INR']['receivable'], '40.00')

    def test_conflicting_note_followup_keeps_amount_unknown(self):
        d = doc('KOTAK', [dict(amount='263.00', note='Electricity bill Alice is supposed to give cash of 131',
                               follow_up='take cash from Alice for bill: 131.5', components=[share('Alice', '131.50')])])
        r = generate([d]); o, = r['obligations']
        self.assertIsNone(o['original_amount'])
        self.assertEqual(o['evidence']['source_supported_amount_mentions'], ['131.00', '131.50'])
        self.assertEqual(len(r['clarification_candidates']), 1)

    def test_receipt_numbers_and_fraction_do_not_establish_original_shares(self):
        d = doc('KOTAK', [dict(amount='530.00', note='I paid for 3 ppl food (my share is 1/3rd, rest they will send: 180 Alice sent and 100 Bob)',
                               components=[share('Alice', '180.00'), share('Bob', '100.00')])])
        r = generate([d])
        self.assertEqual(len(r['obligations']), 2)
        self.assertTrue(all(o['original_amount'] is None for o in r['obligations']))
        self.assertEqual(len(r['clarification_candidates']), 1)

    def test_whole_beneficiary_purchase_requires_explicit_promise(self):
        d = doc('KOTAK', [dict(amount='2364.00', note="train ticket for parents, they'll pay me back", components=[share('parents', '2364.00')])])
        o, = generate([d])['obligations']
        self.assertEqual(o['original_amount'], '2364.00')
        self.assertEqual(o['type'], 'reimbursement')

    def test_four_uncertain_cases_are_questions_not_origins(self):
        ds = [doc('KOTAK', [dict(note='akash wanted me to upi here'), dict(note='akash wanted me to upi there'),
                            dict(amount='10.00', note='paid for smtg of his')]),
              doc('SBI', [dict(direction='credit', amount='10000.00', note='dad sent me money to book my flight tickets')])]
        r = generate(ds)
        self.assertEqual(r['obligations'], [])
        self.assertEqual(len(r['clarification_candidates']), 4)
        self.assertTrue(all(q['answer'] is None for q in r['clarification_candidates']))

    def test_duplicate_component_same_origin_is_not_two_claims(self):
        d = doc('KOTAK', [dict(note='Alice share 40', components=[share(), share()])])
        r = generate([d]); self.assertEqual(len(r['obligations']), 1)

    def test_mentioned_bystander_does_not_become_borrower(self):
        d = doc('KOTAK', [dict(note='Alice share 40; Bob was also there', components=[share(), share('Bob')])])
        r = generate([d])
        self.assertEqual([o['counterparty'] for o in r['obligations']], ['Alice'])

    def test_possible_return_kept_as_context_without_settlement(self):
        d = with_semantics(doc('SBI', [dict(note='Dad will send it back, he needed it temporarily.',
                            narration='UPI/DR/123456789012/Dad/ICIC/dad@bank/UPI'),
                          dict(direction='credit', narration='UPI/CR/223456789012/Dad/ICIC/dad@bank/UPI')]),
                           [dict(payee='Dad')])
        r = generate([d]); o, = r['obligations']
        self.assertTrue(any(c['candidate_transaction_ids'] == ['SBI_0002'] for c in o['existing_relationship_context']))
        self.assertEqual(o['original_amount'], '100.00')
        self.assertEqual(o['settlement_status'], 'UNASSESSED')

    def test_partial_loan_does_not_substitute_gross_principal(self):
        d = with_semantics(doc('SBI', [dict(note='I lent Dad 50 from this payment', amount='100.00')]), [dict(payee='Dad')])
        o, = generate([d])['obligations']
        self.assertIsNone(o['original_amount'])
        self.assertTrue(o['requires_clarification'])

    def test_principal_tampering_is_rejected(self):
        data = inputs([doc('KOTAK', [dict(note='Alice share 40', components=[share()])])])
        r = build(*data); r['obligations'][0]['original_amount'] = '100.00'
        with self.assertRaises(ValueError): validate(r, flatten(data[0]), data[1])

    def test_conflicting_components_and_excess_shares_need_clarification(self):
        d = doc('KOTAK', [dict(note='Alice share 40', components=[share(), share(amount='50.00')]),
                         dict(amount='50.00', note='Alice share 40 and Bob share 40', components=[share(), share('Bob')])])
        r = generate([d])
        self.assertTrue(all(o['original_amount'] is None for o in r['obligations']))

    def test_missing_and_blank_notes_cash_and_transfers_are_not_obligations(self):
        ds = [doc('KOTAK', [dict(direction='credit', narration='UPI/Owner/SBIN/123456789012/spending', note='Monthly allocation'),
                            dict(), dict(note=''), dict(note='Alice gave me 100 in cash, I sent UPI')]),
              doc('SBI', [dict(narration='UPI/DR/123456789012/Owner/KKBK/self/spending'),
                          dict(narration='ATM WDL ATM CASH'),
                          dict(direction='credit', note='Alice sent her share', follow_up='need to send it to kotak')])]
        r = generate(ds)
        self.assertEqual(r['obligations'], [])
        self.assertEqual(r['clarification_candidates'], [])

    def test_amount_correction_keeps_id_and_deterministic_order(self):
        def example(amount):
            return doc('KOTAK', [dict(note=f'Alice share {amount}', components=[share(amount=f'{amount}.00')])])
        a = generate([example(40)]); b = generate([example(45)])
        self.assertEqual(a['obligations'][0]['obligation_id'], b['obligations'][0]['obligation_id'])
        ds = [example(40), doc('SBI', [dict()])]
        self.assertEqual(generate(ds), generate(list(reversed(ds))))

    def test_validation_rejects_duplicate_and_settled_origins(self):
        data = inputs([doc('KOTAK', [dict(note='Alice share 40', components=[share()])])])
        r = build(*data); records = flatten(data[0])
        broken = deepcopy(r); broken['obligations'].append(deepcopy(broken['obligations'][0]))
        with self.assertRaises(ValueError): validate(broken, records, data[1])
        broken = deepcopy(r); broken['obligations'][0]['settlement_status'] = 'OPEN'
        with self.assertRaises(ValueError): validate(broken, records, data[1])

    def test_stale_classification_or_audit_is_rejected(self):
        data = list(inputs([doc('KOTAK', [dict(note='Alice share 40', components=[share()])])]))
        broken = deepcopy(data); broken[2]['transactions'][0]['amount'] = '999.00'
        with self.assertRaises(ValueError): build(*broken)
        broken = deepcopy(data); broken[3]['transactions'][0]['effective_treatment'] = 'expense'
        with self.assertRaises(ValueError): build(*broken)

    def test_write_is_idempotent_and_preserves_all_input_bytes(self):
        data = inputs([doc('KOTAK', [dict(note='Alice share 40', components=[share()])]), doc('SBI', [dict()])])
        with TemporaryDirectory() as temp:
            root = Path(temp)
            content = {f'output/reconciled/{d["bank"].lower()}_reconciled.json': d for d in data[0]}
            content.update({'output/relationships/relationships.json': data[1],
                            'output/classification/effective_transactions.json': data[2],
                            'output/audit/coverage_audit.json': dict(data[3], verification={'source_sha256': {}})})
            for name, value in content.items():
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(value), encoding='utf-8')
            before = {p: p.read_bytes() for p in root.rglob('*.json')}
            run(root); output = root / 'output/obligations/obligations.json'; first = output.read_bytes()
            run(root); self.assertEqual(output.read_bytes(), first)
            self.assertTrue(all(p.read_bytes() == b for p, b in before.items()))
