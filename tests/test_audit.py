"""Synthetic coverage regressions; no private data or API access."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from src.audit import candidates, coverage_counts, inspect_workbook, reason


def row(**changes):
    value = dict(transaction_id='TEST_0001', effective_treatment='unknown',
                 workbook_candidates=['sheet:2'], event_id='EVENT_0001', matched_in_3A=True,
                 meaning_populated=True, follow_up_populated=False,
                 classification_rule='funding_or_beneficiary_terms_unresolved')
    value.update(changes)
    return value


class AuditTests(unittest.TestCase):
    def test_missing_row_blank_note_and_missing_event_are_distinct(self):
        self.assertEqual(reason(row(workbook_candidates=[]))[0], 'missing_workbook_row')
        self.assertEqual(reason(row(meaning_populated=False))[0], 'blank_note')
        self.assertEqual(reason(row(event_id=None))[0], 'missing_generated_event')
        self.assertEqual(reason(row(matched_in_3A=False))[0], 'unmatched_generated_event')
        self.assertEqual(reason(row())[0], 'uncertain_financial_terms')

    def test_unsupported_semantics_require_review(self):
        self.assertEqual(reason(row(classification_rule='no_supported_financial_treatment'))[0],
                         'semantic_classification_review')
        self.assertEqual(reason(row(effective_treatment='expense'))[0], 'supported')

    def test_unknown_and_unmatched_sets_are_not_equivalent(self):
        rows = [row(transaction_id='A', matched_in_3A=False), row(transaction_id='B'),
                row(transaction_id='C', matched_in_3A=False, effective_treatment='cash_conversion')]
        for item in rows:
            item['reason_category'] = reason(item)[0]
        counts = coverage_counts(rows)
        self.assertEqual(counts['unknown'], 2)
        self.assertEqual(counts['unmatched'], 2)
        self.assertEqual(counts['unknown_and_unmatched'], 1)
        self.assertEqual(counts['unknown_but_matched'], 1)
        self.assertEqual(counts['unmatched_but_classified'], 1)

    def test_scan_does_not_stop_at_footer_or_skip_hidden_rows(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'notes.xlsx'
            workbook = Workbook(); sheet = workbook.active
            sheet.append(['Date', 'Details', 'Meaning', 'missed'])
            sheet.append(['01/08/2026', 'Example', 'Lunch', None])
            sheet.append(['Closing Balance', None, None, None])
            sheet.append(['02/08/2026', 'Example', None, 'Check receipt'])
            sheet.row_dimensions[4].hidden = True
            sheet.append([None, None, 'Orphan annotation', None])
            workbook.save(path); workbook.close()
            records, outside = inspect_workbook(path)
            self.assertEqual(set(records), {'Sheet:2', 'Sheet:4'})
            self.assertTrue(records['Sheet:4']['hidden'])
            self.assertEqual(records['Sheet:4']['follow_up'], 'Check receipt')
            self.assertEqual(outside[0]['meaning'], 'Orphan annotation')

    def test_matching_requires_facts_and_narration_not_sequence(self):
        bank = dict(transaction_date='2026-08-01', amount='10.00', direction='debit', raw_description='UPI /ABC')
        note = dict(source_id='sheet:2', date='2026-08-01', amount='10.00', direction='debit', narration='UPI/ A\nBC')
        self.assertEqual(candidates(bank, [note]), [note])
        self.assertEqual(candidates(bank, [dict(note, direction='credit')]), [])
        self.assertEqual(candidates(bank, [dict(note, narration='OTHER')]), [])
        self.assertEqual(len(candidates(bank, [note, dict(note, source_id='sheet:3')])), 2)
