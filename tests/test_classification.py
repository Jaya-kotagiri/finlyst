"""Effective-classification regression tests: synthetic inputs, no API or settlement work."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from tests.support import temporary_directory, doc, share, with_semantics
from src.relationships import derive, flatten
from src.classification import build, run, validate_view


def item(result, identifier):
    return next(t for t in result['transactions'] if t['transaction_id'] == identifier)


class ClassificationTests(unittest.TestCase):
    def test_blinkit_printouts_are_expense_without_relationship(self):
        d = with_semantics(doc('SBI',[dict(amount='78.00',note='Printouts for my application',event_type='purchase')]),
            [dict(merchant='Blinkit',category='office supplies',purpose_context='application printouts',
                  description='Ordered printouts')])
        relationships = derive([d]); self.assertEqual(relationships['transaction_treatments'][0]['classification'],'unclassified')
        t = build([d],relationships)['transactions'][0]
        self.assertEqual(t['effective']['treatment'],'expense')
        self.assertEqual(t['semantic']['purpose_context'],'application printouts')
        self.assertEqual(t['semantic']['merchant'],'Blinkit')
        self.assertEqual(t['semantic']['category'],'office supplies')

    def test_transfer_overrides_income_label_without_changing_event(self):
        docs = [with_semantics(doc('KOTAK',[dict(amount='20000.00',direction='credit',
            narration='UPI/Owner/SBIN/123456789012/spending',note='Salary allocation',event_type='income')]),
            [dict(category='income',purpose_context='monthly spending')]),
            doc('SBI',[dict(amount='20000.00',narration='UPI/DR/123456789012/Owner/KKBK/self/spending')])]
        before=deepcopy(docs);r=build(docs,derive(docs))
        for t in r['transactions']:
            self.assertEqual(t['effective']['treatment'],'self_transfer')
            self.assertFalse(t['effective']['income']);self.assertFalse(t['effective']['expense'])
        self.assertEqual(item(r,'KOTAK_0001')['semantic']['event_type'],'income')
        self.assertEqual(docs,before)

    def test_cross_bank_reimbursement_and_plan(self):
        docs=[doc('KOTAK',[dict(note='Alice share 40',components=[share()])]),
              doc('SBI',[dict(direction='credit',amount='40.00',note='Alice sent her share',
                   follow_up='need to send it to kotak',event_type='income')])]
        r=build(docs,derive(docs));t=item(r,'SBI_0001')
        self.assertEqual(t['effective']['treatment'],'reimbursement_received')
        self.assertFalse(t['effective']['income'])
        self.assertTrue(any(x['status']=='planned' for x in t['relationships']))
        self.assertNotEqual(t['effective']['treatment'],'self_transfer')
        self.assertEqual(item(r,'KOTAK_0001')['effective']['status'],'allocation_deferred')

    def test_cash_movements_not_expenses(self):
        docs=[doc('KOTAK',[dict(narration='ATM WDL ATM CASH 123'),
            dict(amount='500.00',note='Alice gave me 500 in cash because she wanted upi money.',event_type='purchase')])]
        r=build(docs,derive(docs))
        self.assertEqual(r['report']['by_effective_treatment'],{'cash_conversion':2})
        self.assertTrue(all(t['effective']['expense'] is False for t in r['transactions']))

    def test_dad_advance_not_loan_repayment_or_expense(self):
        docs=[doc('SBI',[dict(amount='3000.00',note='Dad will send it back right away, needed it temporarily.',
                             event_type='loan_repayment')])]
        t=build(docs,derive(docs))['transactions'][0]
        self.assertEqual(t['effective']['treatment'],'loan_advance')
        self.assertEqual(t['semantic']['event_type'],'loan_repayment')
        self.assertFalse(t['effective']['expense'])

    def test_unknowns_and_unsupported_purchase_label(self):
        docs=[doc('SBI',[dict(),dict(direction='credit'),dict(note='',event_type='purchase')])]
        r=build(docs,derive(docs))
        self.assertEqual(r['report']['by_effective_treatment'],{'unknown':3})
        self.assertEqual(r['report']['semantic_events'],1)
        self.assertTrue(all(t['effective']['income'] is None for t in r['transactions']))

    def test_payment_on_behalf_not_forced_to_personal_expense(self):
        docs=[with_semantics(doc('KOTAK',[dict(note='paid for smtg of his',event_type='purchase')]),
                            [dict(category='food')])]
        t=build(docs,derive(docs))['transactions'][0]
        self.assertEqual(t['effective']['treatment'],'unknown')
        self.assertEqual(t['semantic']['category'],'food')
        self.assertIn('relationship_unresolved',t['review_reasons'])

    def test_explicit_fee_overrides_generic_purchase_without_notes(self):
        docs=[doc('SBI',[dict(narration='DEBIT ATMCard \n AMC 123',note='',event_type='purchase')])]
        t=build(docs,derive(docs))['transactions'][0]
        self.assertEqual(t['effective']['treatment'],'expense')
        self.assertEqual(t['effective']['source'],'bank_facts')
        self.assertEqual(t['semantic']['event_type'],'purchase')

    def test_all_semantics_preserved_even_without_special_3b_treatment(self):
        docs=[with_semantics(doc('KOTAK',[dict(note='Application fees',event_type='software purchase')]),
                            [dict(category='software',description='Original meaning')])]
        r=build(docs,derive(docs));self.assertEqual(r['report']['by_effective_treatment'],{'expense':1})
        self.assertEqual(r['transactions'][0]['semantic']['description'],'Original meaning')
        self.assertEqual(r['transactions'][0]['provenance']['original_3B_classification'],'unclassified')

    def test_stale_relationships_and_unsupported_special_treatment_fail(self):
        docs=[doc('SBI',[dict()])];relationships=derive(docs)
        broken=deepcopy(relationships);broken['source_fingerprints']['SBI']='bad'
        with self.assertRaises(ValueError):build(docs,broken)
        broken=deepcopy(relationships)
        broken['transaction_treatments'][0].update(classification='self_transfer',income=False,expense=False)
        with self.assertRaises(ValueError):build(docs,broken)

    def test_duplicate_or_lost_bank_and_semantic_records_rejected(self):
        docs=[doc('SBI',[dict(note='Note',event_type='purchase')])];r=build(docs,derive(docs));records=flatten(docs)
        for mutate in (lambda x:x['transactions'].append(deepcopy(x['transactions'][0])),
                       lambda x:x['transactions'][0].update(semantic=None),
                       lambda x:x['transactions'][0].update(amount='1.00')):
            broken=deepcopy(r);mutate(broken)
            with self.assertRaises(ValueError):validate_view(broken,records)

    def test_atomic_idempotent_and_unchanged_inputs(self):
        docs=[doc('SBI',[dict(note='Auto fare',event_type='purchase')]),doc('KOTAK',[dict()])]
        with temporary_directory() as folder:
            root=Path(folder);paths=[root/'sbi.json',root/'kotak.json'];rp=root/'relationships.json';out=root/'effective.json'
            for p,d in zip(paths,docs):p.write_text(json.dumps(d))
            rp.write_text(json.dumps(derive(docs)));before={p:p.read_bytes() for p in paths+[rp]}
            run(paths,rp,out);first=out.read_bytes();run(reversed(paths),rp,out)
            self.assertEqual(first,out.read_bytes());self.assertEqual(before,{p:p.read_bytes() for p in before})
            with self.assertRaises(ValueError):run(paths,rp,rp)


if __name__=='__main__':
    unittest.main()
