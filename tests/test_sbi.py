"""Synthetic workbook tests matching the inspected SBI statement structure."""

import csv
from datetime import datetime
import json
from pathlib import Path
import unittest

from openpyxl import Workbook

from tests.support import temporary_directory
from src.kotak import HEADER as KOTAK_HEADER, parse_kotak
from src.models import FIELDS
from src.sbi import HEADER, SUMMARY_HEADER, FOOTER, parse_sbi
from src.validation import validate_transactions, write_json


class SbiTests(unittest.TestCase):
    def setUp(self):
        self.directory = temporary_directory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'synthetic.xlsx'
        self.rows = [
            ['Synthetic statement'], [None, 'Account Number  :  000123'], [], list(HEADER),
            ['01/08/2026', '  Original narration\nsecond line  ', None, 78, None, '922.00'],
            ['02/08/2026', 'Synthetic credit', 'REF-2', None, '1,000.50', '1922.50'], [],
            ['Statement Summary : 01-08-2026  To  17-09-2026'], list(SUMMARY_HEADER),
            ['1,000.00CR', '1', '1', '78.00', '1,000.50', '1,922.50CR'], [],
            *[[text] for text in FOOTER],
        ]

    def parse(self):
        workbook = Workbook()
        for row in self.rows:
            workbook.active.append(row)
        workbook.save(self.path)
        workbook.close()
        return parse_sbi(self.path)

    def test_normalization_and_original_narration(self):
        result = self.parse()
        self.assertEqual(result[0], dict(
            transaction_id='SBI_0001', bank='SBI', account='000123',
            transaction_date='2026-08-01', amount='78.00', direction='debit',
            raw_description='  Original narration\nsecond line  ', reference=None, balance_after='922.00'))
        self.assertEqual(result[1]['transaction_id'], 'SBI_0002')
        self.assertEqual(result[1]['direction'], 'credit')
        self.assertEqual(result[1]['amount'], '1000.50')
        self.assertEqual(result[1]['reference'], 'REF-2')
        self.assertEqual(result, self.parse())

    def test_optional_fields_and_excel_date(self):
        self.rows[1] = []
        self.rows[4][0] = datetime(2026, 8, 1)
        self.rows[4][5] = None
        first = self.parse()[0]
        self.assertIsNone(first['account'])
        self.assertIsNone(first['balance_after'])
        self.assertEqual(first['transaction_date'], '2026-08-01')

    def test_signed_balance(self):
        self.rows[4][5] = '-78.00'
        self.assertEqual(self.parse()[0]['balance_after'], '-78.00')

    def test_unsafe_rows_reported(self):
        for column, value in [(0, '31/02/2026'), (3, '-1'), (3, '1.001'),
                              (3, None), (4, '5'), (1, None), (3, '=1+2')]:
            with self.subTest(column=column, value=value):
                previous = self.rows[4][column]
                self.rows[4][column] = value
                with self.assertRaisesRegex(ValueError, 'row 5'):
                    self.parse()
                self.rows[4][column] = previous

    def test_unknown_rows_are_not_skipped(self):
        self.rows.insert(6, ['unexpected row'])
        with self.assertRaisesRegex(ValueError, 'row 7'):
            self.parse()

    def test_summary_mismatch(self):
        self.rows[9][1] = '2'
        with self.assertRaisesRegex(ValueError, 'counts/totals differ'):
            self.parse()

    def test_missing_header_and_extra_sheet(self):
        self.rows[3] = ['Unknown header']
        with self.assertRaisesRegex(ValueError, 'incomplete statement'):
            self.parse()
        workbook = Workbook()
        workbook.create_sheet('extra')
        workbook.save(self.path)
        workbook.close()
        with self.assertRaisesRegex(ValueError, 'one statement sheet'):
            parse_sbi(self.path)

    def test_duplicates_retained_and_reported(self):
        self.rows[5] = self.rows[4].copy()
        self.rows[9] = ['1,000.00CR', '2', '0', '156.00', '0.00', '922.00CR']
        result = self.parse()
        self.assertEqual(len(result), 2)
        self.assertEqual(validate_transactions(result)['duplicate_pairs'], [['SBI_0001', 'SBI_0002']])

    def test_schema_matches_kotak_and_json_roundtrip(self):
        sbi = self.parse()
        path = Path(self.directory.name) / 'synthetic.csv'
        with path.open('w', newline='', encoding='utf-8') as source:
            csv.writer(source).writerows([
                KOTAK_HEADER,
                ['1', '01-08-2026 10:00:00', '01-08-2026', 'Synthetic', '', '78', 'DR', '922', 'CR'],
                ['Closing Balance', 'as on 01/08/2026 INR 922.00'],
            ])
        kotak = parse_kotak(path)
        self.assertEqual(tuple(sbi[0]), tuple(kotak[0]))
        self.assertEqual(tuple(sbi[0]), FIELDS)
        destination = Path(self.directory.name) / 'result.json'
        write_json(sbi, destination)
        self.assertEqual(json.loads(destination.read_text(encoding='utf-8')), sbi)

    def test_encrypted_and_invalid_input(self):
        self.path.write_bytes(bytes.fromhex('D0CF11E0A1B11AE1'))
        with self.assertRaisesRegex(ValueError, 'workbook is encrypted'):
            parse_sbi(self.path)
        self.path.write_bytes(b'PK')
        with self.assertRaisesRegex(ValueError, 'not a readable XLSX'):
            parse_sbi(self.path)


if __name__ == '__main__':
    unittest.main()
