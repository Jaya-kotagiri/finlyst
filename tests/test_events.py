"""Synthetic input and mocked API tests; tests never send private data or incur cost."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook
from pydantic import ValidationError

from tests.support import temporary_directory
from src.event_models import EventBatch
from src.events import batch_notes, generate_events, interpret_batch, validate_batch
from src.notes import read_notes


def event(row):
    return dict(source_id=row['source_id'], event_type=None, date=row['date'],
                amount=row['amount'], direction=row['direction'], currency=None,
                merchant=None, payee=None, payer=None, category=None, subcategory=None,
                description='Synthetic meaning', purpose_context=None, entities=[], components=[],
                matching_clues=[], confidence='low', ambiguities=['Purpose unknown'], additional_context=[])


class EventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'notes.xlsx'
        self.rows = [dict(source_id='sheet:2', date='2026-08-01', amount='78.00', direction='debit')]

    def workbook(self, rows):
        w = Workbook()
        for row in rows:
            w.active.append(row)
        w.save(self.path)
        w.close()

    def test_sbi_reader_preserves_followups_and_blank_meanings(self):
        self.workbook([
            [None, 'Date', 'Details', 'Meaning', 'Debit', 'Credit', 'Balance', 'missed'],
            [None, '01/08/2026', 'Original\nnarration', None, '78', None, '22', '  pending repayment  '],
        ])
        row = read_notes(self.path)[0]
        self.assertEqual(row['follow_up'], '  pending repayment  ')
        self.assertNotIn('note', row)
        self.assertNotIn('balance', row)
        self.assertEqual(row['amount'], '78.00')

    def test_kotak_reader_normalizes_facts_not_meanings(self):
        self.workbook([
            ['Transaction Date', 'Description', 'Meaning..', 'Amount', 'Dr / Cr', 'Chq /Ref No.'],
            ['01-08-2026', 'source', 'Ambiguous note', 78, 'CR', 12345],
        ])
        row = read_notes(self.path)[0]
        self.assertEqual(row['reference'], '12345')
        self.assertEqual(row['direction'], 'credit')
        self.assertEqual(row['note'], 'Ambiguous note')

    def test_unsafe_rows_and_formula_rejected(self):
        for amount in ('=1+2', '-1', '1.001'):
            self.workbook([['Date', 'Details', 'Meaning', 'Debit', 'Credit'],
                           ['01/08/2026', 'source', 'note', amount, None]])
            with self.assertRaises(ValueError):
                read_notes(self.path)

    def test_whole_file_and_sequential_sized_batches(self):
        self.assertEqual([len(b) for b in batch_notes(self.rows * 60)], [60])
        self.assertEqual([len(b) for b in batch_notes(self.rows * 125)], [50, 50, 25])
        with self.assertRaises(ValueError):
            batch_notes(self.rows, 1)
        with self.assertRaises(ValueError):
            batch_notes([dict(self.rows[0], note='x' * 80001)])

    def test_coverage_and_source_facts_cannot_change(self):
        valid = {'events': [event(self.rows[0])]}
        validate_batch(valid, self.rows)
        for changed in ({'events': []}, {'events': valid['events'] * 2},
                        {'events': [dict(valid['events'][0], amount='79.00')]},
                        {'events': [dict(valid['events'][0], source_id='invented')]}):
            with self.assertRaises(ValueError):
                validate_batch(changed, self.rows)

    def test_schema_rejects_bad_dates_and_extra_fields(self):
        for change in ({'date': '2026-02-30'}, {'amount': 'NaN'}, {'extra': True}, {'confidence': 'certain'}):
            with self.assertRaises(ValidationError):
                EventBatch.model_validate({'events': [dict(event(self.rows[0]), **change)]})

    def test_unknown_meaning_is_nullable_at_any_amount(self):
        for amount in ('3.01', '30.00', '70.00', '100.00', '500.00'):
            row = dict(self.rows[0], amount=amount)
            candidate = dict(event(row), description=None, category=None, subcategory=None,
                             purpose_context=None, event_type=None)
            parsed = validate_batch({'events': [candidate]}, [row])
            self.assertIsNone(parsed.events[0].description)
            self.assertIsNone(parsed.events[0].category)

    def test_unknown_meaning_rejects_empty_strings(self):
        for field in ('description', 'category', 'subcategory', 'purpose_context', 'event_type'):
            for value in ('', '   '):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    validate_batch({'events': [dict(event(self.rows[0]), **{field: value})]}, self.rows)

    def test_small_known_transaction_keeps_specific_meaning(self):
        row = dict(self.rows[0], amount='3.01')
        candidate = dict(event(row), description='SMS alert fee', category='bank fees',
                         event_type='bank fee', confidence='high', ambiguities=[])
        parsed = validate_batch({'events': [candidate]}, [row])
        self.assertEqual(parsed.events[0].category, 'bank fees')

    def test_structured_api_and_refusal_no_retry(self):
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(status='completed', output=[],
            output_text=json.dumps({'events': [event(self.rows[0])]}),
            usage=SimpleNamespace(input_tokens=20, output_tokens=40))
        interpret_batch(client, self.rows, 'FUTURE', 'model')
        arguments = client.responses.create.call_args.kwargs
        self.assertTrue(arguments['text']['format']['strict'])
        self.assertEqual(arguments['text']['format']['schema'], EventBatch.model_json_schema())
        self.assertFalse(arguments['store'])
        client.responses.create.return_value = SimpleNamespace(status='incomplete',
            incomplete_details=SimpleNamespace(reason='max_output_tokens'))
        with self.assertRaises(ValueError):
            interpret_batch(client, self.rows, 'FUTURE', 'model')
        self.assertEqual(client.responses.create.call_count, 2)

    def test_raw_response_saved_before_json_and_schema_validation(self):
        for text, expected in [('{', 'invalid JSON'),
                               (json.dumps({'events': [{}]}), 'schema validation')]:
            client = Mock()
            client.responses.create.return_value = SimpleNamespace(
                status='completed', output=[], output_text=text,
                model_dump_json=lambda **kwargs: json.dumps({'output_text': text}))
            audit = Path(self.tmp.name) / 'raw.json'
            with self.assertRaisesRegex(ValueError, expected):
                interpret_batch(client, self.rows, 'FUTURE', 'model', audit)
            self.assertEqual(json.loads(audit.read_text())['output_text'], text)
            self.assertTrue(audit.with_suffix('.diagnostic.json').exists())
            client.responses.create.assert_called_once()

    def test_coverage_and_fact_errors_are_reported_together(self):
        rows = self.rows + [dict(self.rows[0], source_id='sheet:3')]
        bad = dict(event(self.rows[0]), amount='99.00', direction='credit')
        with self.assertRaises(ValueError) as caught:
            validate_batch({'events': [bad, bad]}, rows)
        message = str(caught.exception)
        for expected in ('expected=2, returned=2', "missing=['sheet:3']",
                         "duplicate_ids=['sheet:2']", 'source amount', 'source direction'):
            self.assertIn(expected, message)

    def test_refusal_is_distinct_from_truncation(self):
        client = Mock()
        for status, output, reason, expected in [
            ('completed', [SimpleNamespace(type='message', content=[SimpleNamespace(type='refusal')])], None, 'API refusal'),
            ('incomplete', [], 'max_output_tokens', 'incomplete_reason=max_output_tokens'),
        ]:
            client.responses.create.return_value = SimpleNamespace(status=status, output=output,
                incomplete_details=SimpleNamespace(reason=reason))
            with self.assertRaisesRegex(ValueError, expected):
                interpret_batch(client, self.rows, 'FUTURE', 'model')

    def test_merge_save_single_read_and_no_partial_output(self):
        destination = Path(self.tmp.name) / 'events.json'
        rows = [dict(self.rows[0], source_id=f'sheet:{i}') for i in range(1, 71)]
        def interpret(client, batch, bank, model, audit_path=None):
            return EventBatch.model_validate({'events': [event(r) for r in batch]}), {'input_tokens': 1, 'output_tokens': 1}
        with patch('src.events.read_notes', return_value=rows) as read, patch('src.events.interpret_batch', side_effect=interpret) as api:
            report = generate_events(self.path, 'future', destination, Mock())
            read.assert_called_once()
            self.assertEqual(api.call_count, 2)
            self.assertEqual(report['events'], 70)
        saved = destination.read_bytes()
        output = json.loads(saved)
        self.assertEqual(output['events'][-1]['event_id'], 'FUTURE_EVENT_0070')
        with patch('src.events.read_notes', return_value=rows), patch('src.events.interpret_batch', side_effect=ValueError('failed')):
            with self.assertRaises(ValueError):
                generate_events(self.path, 'future', destination, Mock())
        self.assertEqual(destination.read_bytes(), saved)


if __name__ == '__main__':
    unittest.main()
