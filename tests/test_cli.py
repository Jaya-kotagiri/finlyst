"""CLI regressions with disposable synthetic files and mocked network access."""

from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from src import cli, identities, integrity
from src.kotak import HEADER as KOTAK_HEADER
from src.notes import read_notes
from src.sbi import HEADER as SBI_HEADER, SUMMARY_HEADER
from tests.support import doc, event_document, temporary_directory


def inventory(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def workbook(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()


def sources(root):
    """Minimal genuine parser layouts, with already-interpreted synthetic events."""
    path = root / 'input/bank/kotak/statement.csv'
    path.parent.mkdir(parents=True)
    with path.open('w', encoding='utf-8', newline='') as stream:
        csv.writer(stream).writerows([
            ['', '', '', '', 'Account No.', '1234567890'], KOTAK_HEADER,
            ['1', '01-08-2026 10:00:00', '01-08-2026', 'Synthetic kotak', '', '10', 'DR', '90', 'CR'],
            ['Closing Balance', '90'],
        ])
    workbook(root / 'input/bank/sbi/sbi_unencrypted.xlsx', [
        [None, 'Account Number  :  9876543210'], list(SBI_HEADER),
        ['01/08/2026', 'Synthetic sbi', None, 20, None, 180],
        ['Statement Summary : 01-08-2026 To 01-08-2026'], list(SUMMARY_HEADER),
        ['200.00CR', '1', '0', '20.00', '0.00', '180.00CR'],
    ])
    for bank, amount in [('kotak', '10.00'), ('sbi', '20.00')]:
        note_path = root / f'input/notes/{bank}/notes.xlsx'
        workbook(note_path, [['Date', 'Details', 'Meaning', 'Debit', 'Credit'],
                            ['01/08/2026', f'Synthetic {bank}', 'Synthetic purchase', amount, None]])
        rows = read_notes(note_path)
        events = doc(bank.upper(), [dict(note='Synthetic purchase', amount=amount)])['event_source_document']['events']
        events[0]['source_id'] = rows[0]['source_id']
        output = root / f'output/events/{bank}_events.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(event_document(bank.upper(), rows, events)), encoding='utf-8')


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(['--root', str(self.root), *args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def prepare(self):
        sources(self.root)
        code, _, error = self.invoke('run', '--to', 'identities', '--offline')
        self.assertEqual(code, 0, error)

    def generic_integrity(self):
        # Keep all generic financial checks; the existing runner also has assertions
        # specific to the user's 154-row dataset, which this synthetic fixture isn't.
        original = integrity.audit_layers
        return patch('src.integrity.audit_layers', side_effect=lambda *a: original(*a[:8], current_dataset=False))

    def test_full_pipeline_order_and_default_offline(self):
        with patch('src.cli.run_stage') as stage:
            code, output, _ = self.invoke('run')
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] for call in stage.call_args_list], list(cli.STAGES))
        self.assertTrue(all(call.args[1:] == (self.root.resolve(), cli.BANKS, False) for call in stage.call_args_list))
        self.assertIn('[10/10] integrity', output)

    def test_contiguous_range_includes_required_intermediate_stages(self):
        with patch('src.cli.run_stage') as stage:
            self.assertEqual(self.invoke('run', '--from', 'reconcile', '--to', 'integrity', '--offline')[0], 0)
        self.assertEqual([call.args[0] for call in stage.call_args_list], list(cli.STAGES[2:]))

    def test_invalid_arguments_do_not_execute(self):
        cases = [('run', '--from', 'settlements', '--to', 'bank'), ('run', '--from', 'wrong'),
                 ('run', '--offline', '--allow-api'), ('reconcile', '--bank', 'wrong'),
                 ('validate',), ('validate', '--all', '--allow-api'), ('reconcile', '--allow-api'), ()]
        with patch('src.cli.run_stage') as stage, patch('src.cli.validate_all') as validate:
            for args in cases:
                with self.subTest(args=args): self.assertEqual(self.invoke(*args)[0], 2)
            stage.assert_not_called(); validate.assert_not_called()

    def test_individual_stage_and_bank_selection(self):
        with patch('src.cli.run_stage') as stage:
            self.assertEqual(self.invoke('reconcile', '--bank', 'sbi')[0], 0)
            stage.assert_called_once_with('reconcile', self.root.resolve(), ('sbi',), False)

    def test_root_option_before_or_after_command(self):
        for args in [('--root', str(self.root), 'bank'), ('bank', '--root', str(self.root))]:
            with patch('src.cli.run_stage') as stage, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(stage.call_args.args[1], self.root.resolve())

    def test_help_does_not_read_or_write_project_or_execute_stages(self):
        with patch.object(Path, 'resolve', side_effect=AssertionError('touched root')), \
             patch('src.cli.run_stage') as stage, patch('src.cli.validate_all') as validate:
            for command in [('--help',), ('run', '--help'), ('validate', '--help')]:
                self.assertEqual(self.invoke(*command)[0], 0)
            stage.assert_not_called(); validate.assert_not_called()
        self.assertEqual(inventory(self.root), {})

    def test_module_help_works_without_loading_pipeline_dependencies(self):
        code = "import sys; from src import cli; assert 'src.events' not in sys.modules; cli.main(['--help'])"
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=cli.ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('validate', result.stdout)

    def test_failure_stops_later_stages_and_reports_stage(self):
        def failing(stage, *args):
            if stage == 'reconcile': raise ValueError('invalid source schema')
        with patch('src.cli.run_stage', side_effect=failing) as stage:
            code, output, error = self.invoke('run')
        self.assertEqual(code, 1)
        self.assertEqual([c.args[0] for c in stage.call_args_list], ['bank', 'events', 'reconcile'])
        self.assertIn('ERROR [reconcile]: invalid source schema', error)
        self.assertNotIn('Completed.', output)

    def test_missing_root_and_unexpected_error_are_nonzero(self):
        with patch('src.cli.run_stage') as stage:
            self.assertEqual(self.invoke('bank', '--root', str(self.root/'absent'))[0], 1)
            stage.assert_not_called()
        with patch('src.cli.run_stage', side_effect=RuntimeError('private payload')):
            code, _, error = self.invoke('bank')
        self.assertEqual(code, 1)
        self.assertIn('RuntimeError', error)
        self.assertNotIn('private payload', error)

    def test_real_runners_offline_are_idempotent(self):
        sources(self.root)
        with patch('src.events.OpenAI', side_effect=AssertionError('API forbidden')), self.generic_integrity():
            code, _, error = self.invoke('run', '--offline')
            self.assertEqual(code, 0, error)
            before = {p: data[0] for p, data in inventory(self.root).items()}
            self.assertEqual(self.invoke('run')[0], 0)
            self.assertEqual(before, {p: data[0] for p, data in inventory(self.root).items()})
            registry = json.loads((self.root/'output/identities/identity_registry.json').read_bytes())
            self.assertEqual(identities.summary(registry)['records_by_kind']['transaction'], 2)
            self.assertEqual(identities.summary(registry)['unresolved_mappings'], 0)

    def test_missing_events_offline_never_falls_back_to_api(self):
        with patch('src.cli.generate_missing_events') as generate:
            code, _, error = self.invoke('events', '--bank', 'kotak')
            self.assertEqual(code, 1)
            self.assertIn('saved events required offline', error)
            generate.assert_not_called()
        self.assertEqual(inventory(self.root), {})

    def test_existing_events_reused_even_with_allow_api(self):
        self.prepare(); before = inventory(self.root)
        with patch('src.cli.generate_missing_events') as generate:
            self.assertEqual(self.invoke('events', '--allow-api')[0], 0)
            generate.assert_not_called()
        self.assertEqual(inventory(self.root), before)

    def test_explicit_api_permission_generates_only_missing_bank(self):
        self.prepare()
        path = self.root/'output/events/kotak_events.json'; original = path.read_bytes(); path.unlink()
        def generate(root, bank, source, destination):
            self.assertEqual(bank, 'kotak')
            destination.write_bytes(original)
        with patch('src.cli.generate_missing_events', side_effect=generate) as call:
            self.assertEqual(self.invoke('events', '--allow-api')[0], 0)
            call.assert_called_once()

    def test_stale_or_invalid_events_are_not_reinterpreted(self):
        self.prepare()
        path = self.root/'output/events/kotak_events.json'
        saved = json.loads(path.read_bytes()); saved['schema_version'] = 'invalid'
        path.write_text(json.dumps(saved), encoding='utf-8'); before = inventory(self.root)
        with patch('src.cli.generate_missing_events') as generate:
            self.assertEqual(self.invoke('events', '--allow-api')[0], 1)
            generate.assert_not_called()
        self.assertEqual(inventory(self.root), before)

    def test_changed_notes_fail_without_touching_events(self):
        self.prepare()
        workbook(self.root/'input/notes/kotak/notes.xlsx', [['Date', 'Details', 'Meaning', 'Debit', 'Credit'],
                  ['01/08/2026', 'Synthetic kotak', 'Changed source note', '10.00', None]])
        before = inventory(self.root)
        with patch('src.cli.generate_missing_events') as generate:
            code, _, error = self.invoke('events', '--bank', 'kotak', '--allow-api')
            self.assertEqual(code, 1); self.assertIn('do not match current notes', error)
            generate.assert_not_called()
        self.assertEqual(inventory(self.root), before)

    def test_bad_bank_input_blocks_api_before_request(self):
        self.prepare()
        (self.root/'output/events/kotak_events.json').unlink()
        (self.root/'output/bank/kotak_transactions.json').write_text('{}', encoding='utf-8')
        with patch('src.cli.generate_missing_events') as generate:
            self.assertEqual(self.invoke('events', '--bank', 'kotak', '--allow-api')[0], 1)
            generate.assert_not_called()

    def test_api_client_has_no_retries_and_errors_do_not_leak_payload(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key'}), \
             patch('src.events.load_dotenv') as dotenv, patch('src.events.OpenAI') as client, \
             patch('src.events.generate_events', side_effect=RuntimeError('private-note-secret')) as generate:
            with self.assertRaisesRegex(ValueError, 'no automatic retry') as error:
                cli.generate_missing_events(self.root, 'kotak', self.root/'notes.xlsx', self.root/'events.json')
            self.assertNotIn('private-note-secret', str(error.exception))
            self.assertEqual(client.call_args.kwargs['max_retries'], 0)
            generate.assert_called_once()
            dotenv.assert_called_once_with(self.root/'.env', override=False)

    def test_validate_success_and_failure_are_strictly_read_only(self):
        self.prepare()
        with self.generic_integrity():
            self.assertEqual(self.invoke('integrity')[0], 0)
            before = inventory(self.root)
            with patch.object(Path, 'write_text', side_effect=AssertionError('write')), \
                 patch.object(Path, 'write_bytes', side_effect=AssertionError('write')), \
                 patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir')), \
                 patch.object(Path, 'replace', side_effect=AssertionError('replace')), \
                 patch('src.events.OpenAI', side_effect=AssertionError('API')):
                code, _, error = self.invoke('validate', '--all')
                self.assertEqual(code, 0, error)
            self.assertEqual(inventory(self.root), before)
            report = self.root/'output/audit/financial_integrity.json'
            report.write_text('{}', encoding='utf-8'); before = inventory(self.root)
            code, _, error = self.invoke('validate', '--all')
            self.assertEqual(code, 1); self.assertIn('stale', error)
            self.assertEqual(inventory(self.root), before)

    def test_validate_rejects_corrupt_or_missing_registry_without_writes(self):
        self.prepare()
        with self.generic_integrity():
            self.assertEqual(self.invoke('integrity')[0], 0)
            registry = self.root/'output/identities/identity_registry.json'
            registry.write_text('{}', encoding='utf-8')
            before = inventory(self.root)
            self.assertEqual(self.invoke('validate', '--all')[0], 1)
            self.assertEqual(inventory(self.root), before)
            registry.unlink(); before = inventory(self.root)
            self.assertEqual(self.invoke('validate', '--all')[0], 1)
            self.assertEqual(inventory(self.root), before)

    def test_validate_detects_missing_source_mapping_without_repairing_it(self):
        self.prepare()
        with self.generic_integrity():
            self.assertEqual(self.invoke('integrity')[0], 0)
            path = self.root/'output/identities/identity_registry.json'
            registry = json.loads(path.read_bytes())
            key = next(key for key, mapping in registry['legacy_mappings'].items()
                       if mapping['kind'] == 'transaction' and
                       registry['sources'][mapping['source_id']]['role'] == 'bank')
            del registry['legacy_mappings'][key]
            identities.validate_registry(registry)  # Structurally valid, but incomplete for this snapshot.
            path.write_text(json.dumps(registry), encoding='utf-8'); before = inventory(self.root)
            code, _, error = self.invoke('validate', '--all')
            self.assertEqual(code, 1)
            self.assertIn('current source mappings are missing or stale', error)
            self.assertEqual(inventory(self.root), before)

    def test_ambiguous_source_selection_and_unsafe_input_preserve_bank_output(self):
        self.prepare()
        output = self.root/'output/bank/kotak_transactions.json'; before = output.read_bytes()
        source = self.root/'input/bank/kotak/statement.csv'
        extra = source.with_name('other.csv'); extra.write_bytes(source.read_bytes())
        self.assertEqual(self.invoke('bank', '--bank', 'kotak')[0], 1)
        self.assertEqual(output.read_bytes(), before)
        extra.unlink(); source.write_text('invalid CSV', encoding='utf-8')
        self.assertEqual(self.invoke('bank', '--bank', 'kotak')[0], 1)
        self.assertEqual(output.read_bytes(), before)

    def test_integrity_failure_produces_nonzero_exit(self):
        with patch('src.integrity.run', return_value={'result': 'FAIL', 'failed_checks': ['balance']}):
            code, _, error = self.invoke('integrity')
        self.assertEqual(code, 1)
        self.assertIn('balance', error)
