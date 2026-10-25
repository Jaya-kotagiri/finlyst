"""Synthetic tests only; never load private statements in the test suite."""

import csv
import json
from pathlib import Path
import unittest

from tests.support import temporary_directory
from src.kotak import HEADER, parse_kotak
from src.models import FIELDS, money
from src.validation import validate_transaction, validate_transactions, write_json


class KotakTests(unittest.TestCase):
    def setUp(self):
        self.directory = temporary_directory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'synthetic.csv'
        self.rows = [
            ['Account Statement'],
            ['', '', '', '', 'Account No.', '000123'], [], HEADER,
            ['1', '01-08-2026 10:20:30', '31-07-2026', '  Synthetic café\noriginal  ', '', '1,234.50', 'DR', '2,000.00', 'CR'],
            ['2', '02-08-2026 11:00:00', '02-08-2026', 'Synthetic credit', 'REF-2', '50', 'CR', '2,050.00', 'CR'],
            ['Closing Balance', 'as on 02/08/2026 INR 2,050.00'], [], ['Important Note:'],
        ]

    def parse(self):
        with self.path.open('w', encoding='utf-8-sig', newline='') as file:
            csv.writer(file).writerows(self.rows)
        return parse_kotak(self.path)

    def test_parsing_and_normalization(self):
        result = self.parse()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {
            'transaction_id': 'KOTAK_0001', 'bank': 'KOTAK', 'account': '000123',
            'transaction_date': '2026-08-01', 'amount': '1234.50', 'direction': 'debit',
            'raw_description': '  Synthetic café\noriginal  ', 'reference': None,
            'balance_after': '2000.00',
        })
        self.assertEqual(result[1]['direction'], 'credit')
        self.assertEqual(result[1]['amount'], '50.00')
        self.assertEqual(result[1]['reference'], 'REF-2')
        self.assertEqual(result, self.parse())

    def test_missing_optional_values(self):
        self.rows.pop(1)
        self.rows[3][7:9] = ['', '']
        result = self.parse()
        self.assertIsNone(result[0]['account'])
        self.assertIsNone(result[0]['balance_after'])

    def test_debit_balance_is_signed(self):
        self.rows[4][8] = 'DR'
        self.assertEqual(self.parse()[0]['balance_after'], '-2000.00')

    def test_unsafe_transaction_rejected_with_row(self):
        for column, value in [(1, '31-02-2026 10:20:30'), (5, '1.001'), (5, '-5'), (6, 'UNKNOWN')]:
            with self.subTest(column=column, value=value):
                old = self.rows[4][column]
                self.rows[4][column] = value
                with self.assertRaisesRegex(ValueError, 'Kotak row 5'):
                    self.parse()
                self.rows[4][column] = old

    def test_unknown_rows_not_silently_skipped(self):
        self.rows.insert(6, ['Unexpected total'])
        with self.assertRaisesRegex(ValueError, 'Kotak row 7'):
            self.parse()

    def test_missing_closing_balance_rejected(self):
        self.rows = self.rows[:6]
        with self.assertRaisesRegex(ValueError, 'closing balance'):
            self.parse()

    def test_duplicates_reported_and_retained(self):
        result = self.parse()
        duplicate = dict(result[0], transaction_id='KOTAK_0003')
        result.append(duplicate)
        report = validate_transactions(result)
        self.assertEqual(report['duplicate_pairs'], [['KOTAK_0001', 'KOTAK_0003']])
        destination = Path(self.directory.name) / 'output.json'
        self.assertEqual(write_json(result, destination)['validated'], 3)
        self.assertEqual(json.loads(destination.read_text(encoding='utf-8')), result)
        self.assertIn('café', destination.read_text(encoding='utf-8'))

    def test_required_fields_and_types(self):
        transaction = self.parse()[0]
        self.assertEqual(tuple(transaction), FIELDS)
        for field in FIELDS:
            broken = transaction.copy()
            del broken[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                validate_transaction(broken)
        for field, value in [('amount', 1.5), ('amount', '1.1'), ('direction', 'DR'),
                             ('account', 123), ('reference', ''), ('raw_description', ''),
                             ('transaction_date', '2026-02-30'), ('extra', 'value')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_transaction(dict(transaction, **{field: value}))

    def test_money(self):
        for source, expected in [('0', '0.00'), ('1,23,456.70', '123456.70'), ('78.0', '78.00')]:
            self.assertEqual(money(source), expected)
        for source in ['NaN', 'Infinity', '1,2', '1.001', '-1']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                money(source)


if __name__ == '__main__':
    unittest.main()
