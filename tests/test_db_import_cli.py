"""The database importer is explicit and separate from ordinary JSON execution."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from src import cli
from tests.support import temporary_directory


class ImportCliTests(unittest.TestCase):
    def setUp(self):
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_explicit_command_forwards_root_and_reports_import(self):
        with patch('src.db_import.run', return_value={'inserted_rows': 0}) as run, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['import-db', '--root', str(self.root)]), 0)
        run.assert_called_once_with(self.root.resolve())
        self.assertIn('inserted_rows', output.getvalue())

    def test_ordinary_pipeline_never_imports(self):
        with patch('src.db_import.run') as importer, patch('src.cli.run_stage') as stage, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['run', '--offline', '--root', str(self.root)]), 0)
        importer.assert_not_called()
        self.assertEqual([call.args[0] for call in stage.call_args_list], list(cli.STAGES))

    def test_help_and_unsupported_api_flag_do_not_execute(self):
        with patch('src.db_import.run') as importer, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for arguments, status in [(['--help'], 0), (['import-db', '--help'], 0), (['import-db', '--allow-api'], 2)]:
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as result:
                    cli.main(arguments)
                self.assertEqual(result.exception.code, status)
        importer.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_import_failure_returns_nonzero_and_reports_stage(self):
        with patch('src.db_import.run', side_effect=ValueError('Invalid snapshot')), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(cli.main(['import-db', '--root', str(self.root)]), 1)
        self.assertIn('ERROR [import-db]: Invalid snapshot', errors.getvalue())
