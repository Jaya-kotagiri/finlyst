"""Read commands select an explicit snapshot and never run pipeline/import stages."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from src import cli
from tests.support import temporary_directory


SNAPSHOT = '11111111-1111-5111-8111-111111111111'
RECORD = '22222222-2222-5222-8222-222222222222'


class DatabaseReadCliTests(unittest.TestCase):
    def setUp(self):
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def invoke(self, arguments):
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            result = cli.main(['--root', str(self.root), *arguments])
        return result, output.getvalue(), errors.getvalue()

    def test_snapshot_list_is_explicit_read_only_dispatch(self):
        with patch('src.db_read.run', return_value={'snapshots': []}) as run, \
                patch('src.db_import.run') as importer, patch('src.cli.run_stage') as stage:
            result, output, errors = self.invoke(['db-snapshots'])
        self.assertEqual(result, 0)
        self.assertIn('snapshots', output)
        self.assertEqual(errors, '')
        run.assert_called_once_with(self.root.resolve(), view='snapshots')
        importer.assert_not_called()
        stage.assert_not_called()

    def test_default_transaction_view_forwards_explicit_snapshot(self):
        with patch('src.db_read.run', return_value={'records': []}) as run:
            result, _, _ = self.invoke(['db-read', '--snapshot', SNAPSHOT])
        self.assertEqual(result, 0)
        run.assert_called_once_with(self.root.resolve(), view='transactions', snapshot_id=SNAPSHOT,
                                   bank=None, date_from=None, date_to=None, status=None, record_id=None)

    def test_filters_and_subcommand_root_are_forwarded_without_reinterpretation(self):
        for view in ('obligations', 'settlements'):
            with self.subTest(view=view), patch('src.db_read.run', return_value={}) as run, \
                    redirect_stdout(io.StringIO()):
                result = cli.main(['db-read', '--root', str(self.root), '--snapshot', SNAPSHOT,
                                   '--view', view, '--bank', 'sbi', '--date-from', '2026-08-01',
                                   '--date-to', '2026-08-31', '--status', 'UNASSESSED', '--record-id', RECORD])
            self.assertEqual(result, 0)
            run.assert_called_once_with(self.root.resolve(), view=view, snapshot_id=SNAPSHOT,
                                       bank='sbi', date_from='2026-08-01', date_to='2026-08-31',
                                       status='UNASSESSED', record_id=RECORD)

    def test_invalid_filters_fail_before_database_access(self):
        cases = [[], ['--snapshot', 'not-a-uuid'], ['--snapshot', SNAPSHOT, '--record-id', 'not-a-uuid'],
                 ['--snapshot', SNAPSHOT, '--date-from', '2026-02-30'],
                 ['--snapshot', SNAPSHOT, '--date-from', '20260801'],
                 ['--snapshot', SNAPSHOT, '--date-from', '2026-09-01', '--date-to', '2026-08-01'],
                 ['--snapshot', SNAPSHOT, '--status', 'CLOSED'],
                 ['--snapshot', SNAPSHOT, '--view', 'relationships', '--status', 'OPEN'],
                 ['--snapshot', SNAPSHOT, '--view', 'obligations', '--status', 'INVALID'],
                 ['--snapshot', SNAPSHOT, '--bank', 'all'], ['--snapshot', SNAPSHOT, '--allow-api']]
        with patch('src.db_read.run') as run, patch('src.db.connect') as connect, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for arguments in cases:
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as result:
                    cli.main(['db-read', *arguments])
                self.assertEqual(result.exception.code, 2)
        run.assert_not_called()
        connect.assert_not_called()

    def test_help_does_not_import_read_module_or_access_project(self):
        # Block lazy import itself, including when the requested project is absent.
        import sys
        with patch.dict(sys.modules, {'src.db_read': None}), patch('src.db.connect') as connect, \
                patch.object(Path, 'resolve', side_effect=AssertionError('Help accessed project')), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for arguments in (['--help'], ['db-read', '--help'], ['db-snapshots', '--help']):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as result:
                    cli.main(arguments)
                self.assertEqual(result.exception.code, 0)
        connect.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_read_failure_reports_stage_and_nonzero_exit_without_import(self):
        with patch('src.db_read.run', side_effect=ValueError('Snapshot not found')), \
                patch('src.db_import.run') as importer:
            result, _, errors = self.invoke(['db-read', '--snapshot', SNAPSHOT])
        self.assertEqual(result, 1)
        self.assertIn('ERROR [db-read]: Snapshot not found', errors)
        importer.assert_not_called()

    def test_ordinary_pipeline_and_validate_never_query_database(self):
        with patch('src.db_read.run') as reader, patch('src.cli.run_stage') as stages, \
                patch('src.cli.validate_all') as validate, patch('src.db.connect') as connect:
            self.assertEqual(self.invoke(['run', '--offline'])[0], 0)
            self.assertEqual(self.invoke(['validate', '--all'])[0], 0)
        reader.assert_not_called()
        connect.assert_not_called()
        self.assertEqual([call.args[0] for call in stages.call_args_list], list(cli.STAGES))
        validate.assert_called_once_with(self.root.resolve())
