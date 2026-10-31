"""Configuration/unit tests plus explicitly enabled, disposable PostgreSQL tests.

Never point FINLYST_TEST_DB_NAME at a real financial database. Synthetic inserts are
rolled back; the verification harness creates/drops the isolated test database.
"""

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from src import db
from tests.support import temporary_directory


class DatabaseUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(FINLYST_DB_NAME='synthetic', FINLYST_DB_USER='synthetic_user', FINLYST_DB_PASSWORD='synthetic-secret')

    def test_configuration_is_environment_based_and_does_not_load_api_env(self):
        (self.root/'.env').write_text('FINLYST_DB_PASSWORD=wrong-file\nOPENAI_API_KEY=unused', encoding='utf-8')
        with patch('src.db.psycopg.connect') as connect:
            cfg = db.configuration(self.root, self.env)
            connect.assert_not_called()
        self.assertEqual(cfg['password'], 'synthetic-secret')
        self.assertEqual(cfg['host'], '127.0.0.1')
        self.assertEqual(cfg['port'], 55432)

    def test_explicit_environment_overrides_private_database_file(self):
        (self.root/'.env.db').write_text('FINLYST_DB_NAME=filedb\nFINLYST_DB_USER=fileuser\nFINLYST_DB_PASSWORD=file-secret', encoding='utf-8')
        self.assertEqual(db.configuration(self.root, self.env)['dbname'], 'synthetic')
        self.assertEqual(db.configuration(self.root, {})['password'], 'file-secret')

    def test_missing_and_invalid_configuration_errors_are_redacted(self):
        with self.assertRaisesRegex(ValueError, 'FINLYST_DB_PASSWORD'):
            db.configuration(self.root, dict(FINLYST_DB_NAME='test', FINLYST_DB_USER='user'))
        for values in [dict(FINLYST_DB_PORT='secret-value'), dict(FINLYST_DB_PORT='0'),
                       dict(FINLYST_DB_CONNECT_TIMEOUT='999'), dict(FINLYST_DB_SSLMODE='invalid-secret')]:
            with self.subTest(values=values), self.assertRaises(ValueError) as raised:
                db.configuration(self.root, dict(self.env, **values))
            self.assertNotIn('secret-value', str(raised.exception))
            self.assertNotIn('invalid-secret', str(raised.exception))

    def test_connection_error_never_prints_credentials(self):
        with patch('src.db.psycopg.connect', side_effect=psycopg.OperationalError('synthetic-secret')):
            with self.assertRaises(ValueError) as raised:
                db.connect(self.root, environ=self.env)
        self.assertNotIn('synthetic-secret', str(raised.exception))

    def test_help_never_connects_or_creates_files(self):
        with patch('src.db.connect') as connect, redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result: db.main(['--help'])
            self.assertEqual(result.exception.code, 0); connect.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_checksums_are_portable_across_line_endings(self):
        path = self.root/'001_example.sql'
        path.write_bytes(b'SELECT 1;\nSELECT 2;\n'); one = db.migration_files(self.root)
        path.write_bytes(b'SELECT 1;\r\nSELECT 2;\r\n'); two = db.migration_files(self.root)
        self.assertEqual(one, two)

    def test_empty_gapped_duplicate_and_bad_migration_names_rejected(self):
        with self.assertRaises(ValueError): db.migration_files(self.root)
        first = self.root/'001_one.sql'; first.write_text('SELECT 1;', encoding='utf-8')
        for name in ['003_gap.sql', '001_duplicate.sql', 'bad.sql']:
            path = self.root/name; path.write_text('SELECT 1;', encoding='utf-8')
            with self.assertRaises(ValueError): db.migration_files(self.root)
            path.unlink()
        first.write_text('', encoding='utf-8')
        with self.assertRaises(ValueError): db.migration_files(self.root)

    def test_controlled_values_cover_pydantic_contracts(self):
        from typing import get_args
        from src import output_models, identity_models
        script = db.migration_files()[0]['sql']
        for values in [identity_models.Kind, output_models.RelationshipType, output_models.RelationshipStatus,
                       output_models.ObligationType, output_models.SettlementStatus, output_models.ConfirmationMethod]:
            for value in get_args(values):
                self.assertIn("'"+value+"'", script)


@unittest.skipUnless(os.environ.get('FINLYST_TEST_DB_NAME'), 'Set FINLYST_TEST_DB_NAME to an isolated finlyst_test_* database')
class PostgreSQLIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        name = os.environ['FINLYST_TEST_DB_NAME']
        if not re.fullmatch(r'finlyst_test_[a-z0-9_]{1,40}', name):
            raise ValueError('Database tests require an explicitly isolated finlyst_test_* name')
        cls.env = dict(os.environ, FINLYST_DB_NAME=name)
        with db.connect(environ=cls.env) as connection:
            db.migrate(connection)

    def setUp(self):
        self.connection = db.connect(environ=self.env)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.connection.execute('BEGIN')
        self.serial = 0

    def insert(self, table, **values):
        self.connection.execute(sql.SQL('INSERT INTO finlyst.{} ({}) VALUES ({})').format(
            sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, values)),
            sql.SQL(',').join(sql.Placeholder() for _ in values)),
            [Jsonb(v) if isinstance(v, dict) else v for v in values.values()])

    def identity(self, kind, label=None):
        self.serial += 1; label = label or f'synthetic-{self.serial}'
        identifier = uuid5(NAMESPACE_URL, f'finlyst-test:{kind}:{label}')
        seed = hashlib.sha256(f'{kind}:{label}'.encode()).hexdigest()
        self.insert('durable_records', record_id=identifier, record_kind=kind, birth_seed=seed)
        return identifier, self.revision(identifier, kind, label)

    def revision(self, identifier, kind, label):
        revision = uuid5(identifier, label)
        self.insert('interpretation_revisions', revision_id=revision, record_id=identifier,
                    record_kind=kind, interpretation_sha256=hashlib.sha256(label.encode()).hexdigest())
        return revision

    def source(self, label='source'):
        identifier = uuid5(NAMESPACE_URL, 'finlyst-test-source:'+label)
        self.insert('source_artifacts', source_id=identifier, role='synthetic', content_sha256='a'*64, document_sha256='b'*64)
        self.insert('source_documents', source_id=identifier, schema_version=None, payload=Jsonb([]))
        return identifier

    def transaction(self, **changes):
        account, ar = self.identity('account')
        self.insert('accounts', revision_id=ar, record_id=account, bank='KOTAK', account_number='1234567890')
        record, revision = self.identity('transaction')
        values = dict(revision_id=revision, record_id=record, account_id=account, bank='KOTAK',
            transaction_date='2026-08-01', amount=Decimal('100.00'), direction='debit',
            raw_description='Synthetic purchase', reference=None, balance_after=Decimal('-100.00'))
        values.update(changes); self.insert('bank_transactions', **values)
        return record, revision

    def obligation(self, **changes):
        record, revision = self.identity('obligation')
        values = dict(revision_id=revision, record_id=record, obligation_type='loan_advance', direction='receivable',
            counterparty='Synthetic person', purpose=None, original_amount=None, currency='INR',
            currency_basis='owned_INR_bank_statement_scope', origin_date='2026-08-01', origin_status='CONFIRMED',
            settlement_status='UNASSESSED', requires_clarification=True, confidence='high', evidence={}, provenance={})
        values.update(changes); self.insert('obligations', **values)
        return record, revision

    def settlement(self, obligation, **changes):
        record, revision = self.identity('settlement')
        values = dict(revision_id=revision, record_id=record, obligation_id=obligation, direction='receivable',
            currency='INR', original_principal=None, verified_repayments=Decimal('0.00'), verified_zero_repayments=False,
            adjustment=Decimal('0.00'), unadjusted_residual=None, outstanding_amount=None, status='UNASSESSED',
            as_of='2026-08-01', reason='original_principal_unknown', evidence_strength=None,
            confirmation_methods=[], evidence={})
        values.update(changes); self.insert('settlements', **values)
        return record, revision

    def test_migrations_are_idempotent_and_history_is_recorded(self):
        with db.connect(environ=self.env) as connection:
            self.assertEqual(db.migrate(connection), [])
            self.assertEqual(db.status(connection), {'applied':[m['version'] for m in db.migration_files()], 'pending':[]})

    def test_migration_error_rolls_back_ddl_and_history(self):
        with temporary_directory() as temp:
            directory = Path(temp)
            migrations = db.migration_files()
            for migration in migrations:
                shutil.copyfile(db.MIGRATIONS/migration['name'], directory/migration['name'])
            (directory/f'{len(migrations)+1:03d}_failure.sql').write_text('CREATE TABLE public.synthetic_probe (id integer); SELECT undefined_finlyst_function();', encoding='utf-8')
            with db.connect(environ=self.env) as connection:
                with self.assertRaises(psycopg.Error): db.migrate(connection, directory)
                self.assertIsNone(connection.execute("SELECT to_regclass('public.synthetic_probe')").fetchone()[0])
                self.assertEqual(connection.execute('SELECT count(*) FROM public.finlyst_schema_migrations').fetchone()[0], len(migrations))

    def test_applied_checksum_tampering_is_rejected(self):
        with temporary_directory() as temp:
            for migration in db.migration_files():
                shutil.copyfile(db.MIGRATIONS/migration['name'], Path(temp)/migration['name'])
            path = Path(temp)/'001_foundation.sql'
            path.write_text((db.MIGRATIONS/'001_foundation.sql').read_text()+'\n-- altered\n', encoding='utf-8')
            with db.connect(environ=self.env) as connection:
                with self.assertRaisesRegex(ValueError, 'history/checksum'): db.migrate(connection, Path(temp))

    def test_migrations_do_not_commit_callers_pending_work(self):
        with self.assertRaisesRegex(ValueError, 'idle connection'): db.migrate(self.connection)

    def test_exact_numeric_rejects_precision_nonfinite_and_negative_unsigned_values(self):
        for value in ('1.001', 'NaN', 'Infinity', '-Infinity'):
            with self.subTest(value=value), self.assertRaises(psycopg.errors.CheckViolation):
                with self.connection.transaction():
                    self.connection.execute('SELECT %s::finlyst.money_amount', (Decimal(value),))
        for domain, value in [('nonnegative_amount','-1.00'), ('positive_amount','0.00')]:
            with self.assertRaises(psycopg.errors.CheckViolation), self.connection.transaction():
                self.connection.execute(sql.SQL('SELECT %s::finlyst.{}').format(sql.Identifier(domain)), (Decimal(value),))
        amount = Decimal('123456789012345678901234567890.12')
        self.assertEqual(self.connection.execute('SELECT %s::finlyst.money_amount', (amount,)).fetchone()[0], amount)

    def test_unknowns_stay_null_and_signed_balance_is_preserved(self):
        transaction, _ = self.transaction()
        obligation, _ = self.obligation()
        _, revision = self.settlement(obligation)
        self.assertEqual(self.connection.execute('SELECT original_principal,outstanding_amount,status FROM finlyst.settlements WHERE revision_id=%s', (revision,)).fetchone(), (None,None,'UNASSESSED'))
        self.assertEqual(self.connection.execute('SELECT balance_after FROM finlyst.bank_transactions WHERE record_id=%s', (transaction,)).fetchone()[0], Decimal('-100.00'))

    def test_unassessed_closed_and_unknown_principal_contracts(self):
        obligation, _ = self.obligation()
        for change in [dict(outstanding_amount=Decimal('0.00')), dict(status='CLOSED',outstanding_amount=Decimal('0.00')),
                       dict(original_principal=Decimal('10.00'),status='CLOSED',outstanding_amount=Decimal('1.00')),
                       dict(status='invented')]:
            with self.subTest(change=change), self.assertRaises(psycopg.errors.CheckViolation), self.connection.transaction():
                self.settlement(obligation, **change)
        self.settlement(obligation, original_principal=Decimal('10.00'), verified_repayments=Decimal('12.00'),
                        adjustment=Decimal('-2.00'), unadjusted_residual=Decimal('-2.00'), outstanding_amount=Decimal('0.00'),
                        status='CLOSED', evidence_strength='high', confirmation_methods=['inferred'])

    def test_wrong_kind_dangling_and_wrong_revision_references_rejected(self):
        transaction, revision = self.transaction()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), self.connection.transaction():
            self.transaction(account_id=transaction)
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), self.connection.transaction():
            self.insert('revision_references', revision_id=revision, role='missing', target_record_id=uuid5(NAMESPACE_URL,'missing'))
        other, other_revision = self.identity('account')
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), self.connection.transaction():
            self.insert('accounts', revision_id=other_revision, record_id=transaction, bank='SBI', account_number=None)

    def test_revisions_preserve_identity_across_counterparty_corrections(self):
        record, first = self.obligation()
        second = self.revision(record, 'obligation', 'corrected-name')
        columns = [r[0] for r in self.connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='finlyst' AND table_name='obligations' ORDER BY ordinal_position")]
        row = dict(zip(columns, self.connection.execute('SELECT * FROM finlyst.obligations WHERE revision_id=%s', (first,)).fetchone()))
        row.update(revision_id=second, counterparty='Corrected synthetic spelling')
        self.insert('obligations', **row)
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT record_id),count(*) FROM finlyst.obligations').fetchone(), (1,2))

    def test_scoped_legacy_ids_and_unresolved_history(self):
        source = self.source('first'); other = self.source('second')
        record, revision = self.transaction()
        mappings = []
        for source_id in (source, other):
            mapping = uuid5(source_id, 'KOTAK_0001'); mappings.append(mapping)
            self.insert('legacy_mappings', mapping_id=mapping, source_id=source_id, record_kind='transaction', legacy_id='KOTAK_0001', record_sha256='a'*64)
            self.insert('identity_decisions', mapping_id=mapping, decision_order=1, record_kind='transaction', status='resolved',
                        record_id=record, revision_id=revision, reason='same supported movement', unresolved_references={}, continuity=None)
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT record_id) FROM finlyst.identity_decisions').fetchone()[0], 1)
        self.insert('identity_decisions', mapping_id=mappings[0], decision_order=2, record_kind='transaction', status='unresolved',
                    record_id=None, revision_id=None, reason='new conflicting evidence', unresolved_references={}, continuity=None)
        self.insert('decision_candidates', mapping_id=mappings[0], decision_order=2, candidate_id=record, record_kind='transaction')
        with self.assertRaises(psycopg.errors.CheckViolation), self.connection.transaction():
            self.insert('identity_decisions', mapping_id=mappings[0], decision_order=3, record_kind='transaction', status='unresolved',
                        record_id=record, revision_id=revision, reason='invalid', unresolved_references={}, continuity=None)

    def test_ambiguous_evidence_keys_are_not_forced_unique(self):
        first, _ = self.identity('transaction'); second, _ = self.identity('transaction')
        for record in (first, second): self.insert('identity_keys', record_id=record, identity_key='c'*64)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM finlyst.identity_keys').fetchone()[0], 2)

    def test_classification_unknown_flags_and_source_scope(self):
        record, _ = self.transaction()
        for source in (self.source('one'), self.source('two')):
            self.insert('effective_classifications', source_document_id=source, transaction_id=record, event_id=None,
                        treatment='unknown', income=None, expense=None, status='unknown', classification_source='insufficient_evidence',
                        rule='no_supported_financial_treatment', confidence='low', semantic=None, evidence={})
        self.assertEqual(self.connection.execute('SELECT income,expense,semantic FROM finlyst.effective_classifications LIMIT 1').fetchone(), (None,None,None))

    def test_allocations_link_correct_obligation_and_keep_inferred_confirmation(self):
        origin, _ = self.obligation(original_amount=Decimal('100.00')); transaction, _ = self.transaction(direction='credit')
        allocation, revision = self.identity('allocation')
        self.insert('allocations', revision_id=revision, record_id=allocation, obligation_id=origin, repayment_transaction_id=transaction,
            amount=Decimal('100.00'), currency='INR', rule='unique_immediate_bank_counterparty_return', explicit_split=False,
            full_settlement_intent=True, explicit_minor_overpayment=False, confidence='high', evidence_strength='high',
            confirmation_method='inferred', evidence={'note':'Synthetic evidence only'})
        _, settlement = self.settlement(origin, original_principal=Decimal('100.00'), verified_repayments=Decimal('100.00'),
            outstanding_amount=Decimal('0.00'), status='CLOSED', evidence_strength='high', confirmation_methods=['inferred'])
        self.insert('settlement_allocations', settlement_revision_id=settlement, allocation_revision_id=revision,
                    allocation_record_id=allocation, obligation_id=origin)
        other, _ = self.obligation()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), self.connection.transaction():
            _, other_settlement = self.settlement(other)
            self.insert('settlement_allocations', settlement_revision_id=other_settlement, allocation_revision_id=revision,
                        allocation_record_id=allocation, obligation_id=other)
        self.assertEqual(self.connection.execute('SELECT evidence_strength,confirmation_method FROM finlyst.allocations').fetchone(), ('high','inferred'))

    def test_source_json_and_schema_versions_preserve_annotations(self):
        identifier = uuid5(NAMESPACE_URL,'synthetic-document')
        payload = dict(schema_version='1.0', local_corrections=[{'before':'', 'after':None}], original_response_sha256='d'*64)
        self.insert('source_artifacts', source_id=identifier, role='events', content_sha256='e'*64, document_sha256='f'*64)
        self.insert('source_documents', source_id=identifier, schema_version='1.0', payload=payload)
        self.assertEqual(self.connection.execute('SELECT payload FROM finlyst.source_documents WHERE source_id=%s',(identifier,)).fetchone()[0], payload)

    def test_semantic_and_relationship_links_preserve_unknown_context(self):
        transaction, _ = self.transaction()
        event, er = self.identity('event')
        context = dict(entities=[], components=[], matching_clues=['synthetic clue'], ambiguities=['unknown purpose'], additional_context=[])
        self.insert('semantic_events', revision_id=er, record_id=event, transaction_id=transaction,
                    confidence='low', context=context)
        relationship, rr = self.identity('relationship')
        self.insert('relationships', revision_id=rr, record_id=relationship, relationship_type='planned_transfer',
                    status='planned', rule='explicit_future_transfer_instruction', confidence='high', purpose='Synthetic plan',
                    financial_treatment={'income':None,'expense':None}, evidence=Jsonb([]), details={'execution':'not_established'})
        self.insert('relationship_transactions', relationship_revision_id=rr, transaction_id=transaction, role='participant')
        self.insert('revision_references', revision_id=rr, role='events', target_record_id=event)
        self.assertEqual(self.connection.execute('SELECT amount,description,category,context FROM finlyst.semantic_events').fetchone(),
                         (None,None,None,context))
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), self.connection.transaction():
            self.insert('relationship_transactions', relationship_revision_id=rr, transaction_id=event, role='candidate')

    def test_record_deletion_is_restricted_and_ids_are_uuid(self):
        record, _ = self.transaction()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), self.connection.transaction():
            self.connection.execute('DELETE FROM finlyst.durable_records WHERE record_id=%s', (record,))
        with self.assertRaises(psycopg.errors.InvalidTextRepresentation), self.connection.transaction():
            self.connection.execute("INSERT INTO finlyst.durable_records VALUES ('bad-uuid','account',%s)", ('a'*64,))

    def test_core_relational_tables_and_indexes_exist(self):
        tables = {row[0] for row in self.connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='finlyst'")}
        self.assertTrue({'accounts','bank_transactions','semantic_events','relationships','effective_classifications',
                         'obligations','settlements','allocations','source_artifacts','legacy_mappings','interpretation_revisions'} <= tables)
        count = self.connection.execute("SELECT count(*) FROM pg_indexes WHERE schemaname='finlyst'").fetchone()[0]
        self.assertGreaterEqual(count, 25)
