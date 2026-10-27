"""Synthetic 3A tests; no network or private fixtures."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from tests.support import temporary_directory, event_document
from src.reconcile import digest, reconcile, run, validate_result, reference_precision


def transaction(i, narration='Synthetic purchase', amount='30.00', day='2026-08-01', ref=None):
    return dict(transaction_id=f'KOTAK_{i:04d}', bank='KOTAK', account=None,
                transaction_date=day, amount=amount, direction='debit', raw_description=narration,
                reference=ref, balance_after=None)


def document(bank, indices):
    rows, events = [], []
    for i, bi in enumerate(indices, 1):
        t = bank[bi]
        row = dict(source_id=f'notes:{i * 3}', date=t['transaction_date'], amount=t['amount'],
                   direction=t['direction'], narration=t['raw_description'])
        if t['reference']:
            row['reference'] = t['reference']
        rows.append(row)
        events.append(dict(event_id=f'KOTAK_EVENT_{i:04d}', source_id=row['source_id'],
            event_type=None, date=row['date'], amount=row['amount'], direction=row['direction'],
            currency=None, merchant=None, payee=None, payer=None, category=None, subcategory=None,
            description=None, purpose_context=None, entities=[], components=[], matching_clues=[],
            confidence='low', ambiguities=['Unknown purpose'], additional_context=[]))
    return event_document('KOTAK', rows, events)


def refresh(doc):
    doc['source_rows_sha256'] = digest(doc['source_rows'])


class ReconcileTests(unittest.TestCase):
    def test_reference_rounding_and_scientific_notation(self):
        for source in ('623415000000', '6.23E+11', '6.23415e11', '6.23414565100E+11'):
            bank = [transaction(1, 'Card payment Example Shop', ref='623414565100')]
            doc = document(bank, [0]); doc['source_rows'][0]['reference'] = source; refresh(doc)
            doc['events'][0].update(merchant='Example Shop', confidence='high', ambiguities=[])
            result = reconcile(bank, doc)
            self.assertEqual(result['report']['matched'], 1)
            self.assertEqual(result['report']['ambiguous_events'], 0)
            row = result['transactions'][0]
            self.assertFalse(row['meaning_needs_review'])
            self.assertEqual(row['bank_transaction']['reference'], '623414565100')
            self.assertEqual(result['event_source_document']['source_rows'][0]['reference'], source)
            self.assertEqual(row['matching_evidence']['supported_merchant_payee_names'], ['Example Shop'])

    def test_precision_does_not_override_other_conflicts(self):
        bank = [transaction(1, ref='623414565100')]
        for source in ('623416000000', '6.24E+11', '6E+11', 'UPI-623415000000', '0623415000000'):
            doc = document(bank, [0]); doc['source_rows'][0]['reference'] = source; refresh(doc)
            self.assertEqual(reconcile(bank, doc)['report']['matched'], 0)
        doc = document(bank, [0]); doc['source_rows'][0].update(reference='6.23E+11', narration='different'); refresh(doc)
        self.assertEqual(reconcile(bank, doc)['report']['matched'], 0)
        self.assertIsNone(reference_precision('6.23E+11', '0623414565100'))

    def test_precision_collision_remains_ambiguous(self):
        bank = [transaction(1, ref='623414565100'), transaction(2, ref='623414565101')]
        doc = document(bank, [0]); doc['source_rows'][0]['reference'] = '6.23E+11'; refresh(doc)
        result = reconcile(bank, doc)
        self.assertEqual(result['report']['matched'], 0)
        self.assertEqual(result['report']['ambiguous_events'], 1)

    def test_precision_match_still_requires_sequence_and_facts(self):
        bank = [transaction(1, 'one', ref='623414565100'), transaction(2, 'two', ref='723414565100')]
        doc = document(bank, [1, 0])
        for row in doc['source_rows']:
            row['reference'] = row['reference'][0] + '.23E+11'
        refresh(doc)
        self.assertEqual(reconcile(bank, doc)['report']['matched'], 0)
        for field, value in [('date', '2026-08-09'), ('amount', '31.00'), ('direction', 'credit')]:
            doc = document(bank, [0]); doc['source_rows'][0].update(reference='6.23E+11')
            doc['source_rows'][0][field] = value; doc['events'][0][field] = value; refresh(doc)
            self.assertEqual(reconcile(bank, doc)['report']['matched'], 0)

    def test_gaps_and_unknown_meaning_preserved(self):
        bank = [transaction(1, 'one'), transaction(2, 'two'), transaction(3, 'three')]
        doc = document(bank, [0, 2])
        result = reconcile(bank, doc)
        self.assertEqual(result['report']['matched'], 2)
        self.assertEqual(result['report']['unmatched_bank'], 1)
        self.assertEqual([t['bank_transaction'] for t in result['transactions']], bank)
        self.assertTrue(all(t['category'] is None for t in result['transactions']))
        self.assertIsNone(result['transactions'][1]['event'])
        self.assertEqual(result['event_source_document'], doc)

    def test_duplicate_amounts_distinct_narrations(self):
        bank = [transaction(1, 'one'), transaction(2, 'two')]
        result = reconcile(bank, document(bank, [0, 1]))
        self.assertEqual(result['report']['matched'], 2)

    def test_indistinguishable_duplicates_remain_ambiguous(self):
        bank = [transaction(1), transaction(2)]
        result = reconcile(bank, document(bank, [0, 1]))
        self.assertEqual(result['report']['matched'], 0)
        self.assertEqual(result['report']['ambiguous_bank'], 2)
        self.assertEqual(result['report']['ambiguous_events'], 2)

    def test_sequence_anchors_disambiguate_with_missing_notes(self):
        bank = [transaction(1, 'same'), transaction(2, 'anchor'), transaction(3, 'same')]
        result = reconcile(bank, document(bank, [1, 2]))
        self.assertEqual(result['report']['matched'], 2)
        self.assertEqual(result['transactions'][0]['match_status'], 'unmatched')
        self.assertEqual(result['transactions'][2]['event']['event_id'], 'KOTAK_EVENT_0002')

    def test_crossing_same_day_anchors_do_not_force_matches(self):
        bank = [transaction(1, 'one'), transaction(2, 'two')]
        result = reconcile(bank, document(bank, [1, 0]))
        self.assertEqual(result['report']['matched'], 0)
        self.assertEqual(result['report']['ambiguous_events'], 2)

    def test_chronology_used_for_different_days(self):
        bank = [transaction(1, 'one', day='2026-08-02'), transaction(2, 'two')]
        self.assertEqual(reconcile(bank, document(bank, [1, 0]))['report']['matched'], 2)

    def test_whitespace_wrapping_only(self):
        bank = [transaction(1, 'UPI/REF/U P I')]
        doc = document(bank, [0]); doc['source_rows'][0]['narration'] = 'UPI/REF/UP\nI'; refresh(doc)
        self.assertEqual(reconcile(bank, doc)['report']['matched'], 1)
        doc['source_rows'][0]['narration'] = 'other'; refresh(doc)
        result = reconcile(bank, doc)
        self.assertEqual(result['report']['ambiguous_events'], 1)
        self.assertEqual(result['report']['matched'], 0)

    def test_reference_match_and_conflict(self):
        bank = [transaction(1, ref='R1')]; doc = document(bank, [0])
        doc['source_rows'][0]['narration'] = 'different'; refresh(doc)
        self.assertEqual(reconcile(bank, doc)['report']['matched'], 1)
        doc['source_rows'][0]['reference'] = 'R2'; refresh(doc)
        result = reconcile(bank, doc)
        self.assertEqual(result['report']['ambiguous_events'], 1)
        self.assertEqual(result['event_decisions'][0]['reason'], 'reference_conflict')
        self.assertIsNone(result['transactions'][0]['event'])

    def test_date_amount_direction_must_agree(self):
        bank = [transaction(1)]
        for key, value in [('date', '2026-08-02'), ('amount', '31.00'), ('direction', 'credit')]:
            doc = document(bank, [0]); doc['source_rows'][0][key] = value; doc['events'][0][key] = value; refresh(doc)
            self.assertEqual(reconcile(bank, doc)['report']['unmatched_events'], 1)

    def test_bad_provenance_and_result_rejected(self):
        bank = [transaction(1)]; doc = document(bank, [0])
        broken = deepcopy(doc); broken['source_rows'][0]['amount'] = '5.00'
        with self.assertRaises(ValueError): reconcile(bank, broken)
        broken = deepcopy(doc); broken['events'][0]['amount'] = '5.00'
        with self.assertRaises(ValueError): reconcile(bank, broken)
        result = reconcile(bank, doc); result['transactions'][0]['category'] = 'invented'
        with self.assertRaises(ValueError): validate_result(result, bank, doc)

    def test_atomic_idempotent_output_and_inputs_unchanged(self):
        with temporary_directory() as folder:
            root = Path(folder); bank = [transaction(1)]; doc = document(bank, [0])
            bp, ep, out = root/'bank.json', root/'events.json', root/'reconciled.json'
            bp.write_text(json.dumps(bank)); ep.write_text(json.dumps(doc))
            before = bp.read_bytes(), ep.read_bytes()
            run(bp, ep, out); first = out.read_bytes(); run(bp, ep, out)
            self.assertEqual(first, out.read_bytes())
            self.assertEqual(before, (bp.read_bytes(), ep.read_bytes()))
            with self.assertRaises(ValueError): run(bp, ep, ep)
            self.assertEqual(before, (bp.read_bytes(), ep.read_bytes()))


if __name__ == '__main__':
    unittest.main()
