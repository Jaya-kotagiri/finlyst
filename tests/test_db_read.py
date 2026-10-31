"""Read-only database query regressions against isolated synthetic imports."""

from copy import deepcopy
import os
import re
import unittest
from unittest.mock import patch
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

from src import db, db_import, identities
from tests.import_support import resolved_snapshot, snapshot
from tests.support import origin, receipt


@unittest.skipUnless(os.environ.get('FINLYST_TEST_DB_NAME'),
                     'Set FINLYST_TEST_DB_NAME to an isolated finlyst_test_* database')
class DatabaseReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        name = os.environ['FINLYST_TEST_DB_NAME']
        if not re.fullmatch(r'finlyst_test_[a-z0-9_]{1,40}', name):
            raise ValueError('Read tests require an explicitly isolated finlyst_test_* database')
        cls.env = dict(os.environ, FINLYST_DB_NAME=name)
        with db.connect(environ=cls.env) as connection:
            db.migrate(connection)

    def setUp(self):
        self.connection = db.connect(environ=self.env, autocommit=True)
        self.addCleanup(self.connection.close)
        self.tables = sorted(row[0] for row in self.connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='finlyst'"))
        self.clear()
        self.addCleanup(self.clear)

    def clear(self):
        name = self.connection.execute('SELECT current_database()').fetchone()[0]
        if name != self.env['FINLYST_DB_NAME'] or not re.fullmatch(r'finlyst_test_[a-z0-9_]{1,40}', name):
            raise ValueError('Refusing to clear a non-disposable database')
        self.connection.execute(sql.SQL('TRUNCATE TABLE {}').format(sql.SQL(',').join(
            sql.Identifier('finlyst', table) for table in self.tables)))

    def rows(self):
        return {table: sorted(self.connection.execute(sql.SQL('SELECT * FROM finlyst.{}').format(
            sql.Identifier(table))).fetchall(), key=repr) for table in self.tables}

    def import_snapshot(self, data=None, registry=None):
        if data is None:
            data, registry = resolved_snapshot()
        result = db_import.import_snapshot(self.connection, data, registry)
        return result['snapshot_id'], data, registry

    def view(self, snapshot_id, name, **filters):
        from src import db_read
        return db_read.read_view(self.connection, snapshot_id, name, **filters)

    def test_reconstructs_exact_sources_registry_and_lists_explicit_snapshot(self):
        from src import db_read
        identifier, original, registry = self.import_snapshot()
        result = db_read.read_snapshot(self.connection, identifier)
        self.assertEqual(result['snapshot_id'], identifier)
        self.assertEqual(result['snapshot'], original)
        self.assertEqual(result['registry'], registry)
        self.assertEqual(set(result['sources']), set(db_import.documents(original)))
        for value in result['sources'].values():
            self.assertEqual(str(UUID(value)), value)
        listing = db_read.list_snapshots(self.connection)
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]['snapshot_id'], identifier)
        self.assertTrue(listing[0]['imported_at'])
        self.assertEqual(listing[0]['counts']['transaction'], 4)
        self.assertEqual(listing[0]['counts']['event'], 2)

    def test_transaction_view_preserves_all_facts_semantics_and_provenance(self):
        identifier, original, registry = self.import_snapshot()
        result = self.view(identifier, 'transactions')
        self.assertEqual((result['schema_version'], result['view'], result['count']), ('3D.3.1', 'transactions', 4))
        bank_rows = {row['transaction_id']: row for rows in original['banks'].values() for row in rows}
        classifications = {row['transaction_id']: row for row in original['classification']['transactions']}
        events = {row['bank_transaction']['transaction_id']: row['event']
                  for document in original['reconciled'] for row in document['transactions']}
        self.assertEqual({row['legacy_id'] for row in result['records']}, set(bank_rows))
        self.assertEqual(len({row['record_id'] for row in result['records']}), 4)
        for row in result['records']:
            legacy = row['legacy_id']
            self.assertEqual(row['bank_transaction'], bank_rows[legacy])
            self.assertEqual(row['classification'], classifications[legacy])
            self.assertEqual(row['event'], events[legacy])
            self.assertEqual(registry['revisions'][row['revision_id']]['record_id'], row['record_id'])
            self.assertTrue(row['provenance'])
            for evidence in row['provenance']:
                mapping = registry['legacy_mappings'][evidence['mapping_id']]
                self.assertEqual(evidence['decision'], mapping['decisions'][-1])
                self.assertEqual(identities.fingerprint(evidence['payload']), mapping['record_sha256'])
        self.assertEqual(result, self.view(identifier, 'transactions'))

    def test_actual_database_transaction_is_repeatable_read_and_read_only(self):
        from src import db_read
        identifier, _, _ = self.import_snapshot()
        before = self.rows()
        verify = db_import.verify_plan
        observed = []
        def inspect(connection, plan):
            observed.append(connection.execute('SHOW transaction_read_only').fetchone()[0])
            self.assertEqual(connection.execute('SHOW transaction_isolation').fetchone()[0], 'repeatable read')
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                with connection.transaction():
                    connection.execute("UPDATE finlyst.bank_transactions SET raw_description='Must not be written'")
            return verify(connection, plan)
        with patch('src.db_import.verify_plan', side_effect=inspect):
            db_read.read_snapshot(self.connection, identifier)
        self.assertEqual(observed, ['on'])
        self.assertEqual(self.connection.info.transaction_status, TransactionStatus.IDLE)
        self.assertEqual(before, self.rows())

    def test_read_uses_database_evidence_without_loading_or_writing_project_files(self):
        from src import db_read
        identifier, original, _ = self.import_snapshot()
        with (patch('src.db_import.load_project', side_effect=AssertionError('No source file access')),
             patch('pathlib.Path.read_bytes', side_effect=AssertionError('No file reading')),
             patch('pathlib.Path.write_bytes', side_effect=AssertionError('No file writing')),
             patch('pathlib.Path.write_text', side_effect=AssertionError('No file writing'))):
            self.assertEqual(db_read.read_snapshot(self.connection, identifier)['snapshot'], original)
            self.assertEqual(self.view(identifier, 'transactions')['count'], 4)

    def test_bank_date_and_durable_record_filters_are_inclusive(self):
        identifier, _, _ = self.import_snapshot()
        result = self.view(identifier, 'transactions', bank='kotak', date_from='2026-08-02', date_to='2026-08-03')
        self.assertEqual([row['legacy_id'] for row in result['records']], ['KOTAK_0002', 'KOTAK_0003'])
        selected = result['records'][0]
        self.assertEqual(self.view(identifier, 'transactions', record_id=selected['record_id'])['records'], [selected])
        self.assertEqual(self.view(identifier, 'transactions', date_from='2027-01-01')['count'], 0)

    def test_invalid_filters_and_snapshot_identifiers_are_rejected(self):
        from src import db_read
        identifier, _, _ = self.import_snapshot()
        for invalid in ('invalid-uuid', '', None, str(UUID(int=0))):
            with self.subTest(snapshot=invalid), self.assertRaises(ValueError):
                db_read.read_snapshot(self.connection, invalid)
        for fields in [dict(bank='UNKNOWN'), dict(date_from='2026-02-30'),
                       dict(date_from='2026-09-01', date_to='2026-08-01'), dict(record_id='legacy-123')]:
            with self.subTest(filters=fields), self.assertRaises(ValueError):
                self.view(identifier, 'transactions', **fields)
        with self.assertRaises(ValueError):
            self.view(identifier, 'nonexistent')
        with self.assertRaises(ValueError):
            self.view(identifier, 'settlements', status='invented')

    def test_pending_caller_work_is_never_committed(self):
        from src import db_read
        identifier, _, _ = self.import_snapshot()
        before = self.rows()
        with self.connection.transaction():
            for function in (lambda: db_read.list_snapshots(self.connection),
                             lambda: db_read.read_snapshot(self.connection, identifier),
                             lambda: self.view(identifier, 'transactions')):
                with self.assertRaisesRegex(ValueError, 'idle'):
                    function()
        self.assertEqual(before, self.rows())

    def test_unknown_principal_and_unassessed_outstanding_are_null(self):
        item = origin()
        item['note'] = 'Alice share; she will send'
        item['components'][0]['amount'] = None
        data, registry = resolved_snapshot({'KOTAK': [item], 'SBI': [dict()]})
        identifier, _, _ = self.import_snapshot(data, registry)
        row, = self.view(identifier, 'obligations', status='UNASSESSED')['records']
        self.assertIsNone(row['obligation']['original_amount'])
        self.assertEqual(row['settlement']['status'], 'UNASSESSED')
        self.assertIsNone(row['settlement']['outstanding_amount'])
        self.assertEqual(row['allocations'], [])
        self.assertEqual(self.view(identifier, 'obligations', status='CLOSED')['count'], 0)

    def test_dad_repayment_retains_high_inferred_confirmation(self):
        data, registry = resolved_snapshot({'KOTAK': [dict()], 'SBI': [
            dict(amount='3000.00', note='Dad will send it back right away, he needed it temporarily.',
                 narration='UPI/DR/123456789012/Dad/ICIC/dad@bank/UPI'),
            dict(amount='3000.00', direction='credit',
                 narration='UPI/CR/223456789012/Dad/ICIC/dad@bank/UPI')]},
            {'SBI': [dict(payee='Dad')]})
        identifier, _, _ = self.import_snapshot(data, registry)
        row, = self.view(identifier, 'obligations')['records']
        allocation, = row['allocations']
        self.assertEqual((allocation['data']['evidence_strength'], allocation['data']['confirmation_method']),
                         ('high', 'inferred'))
        self.assertEqual((row['settlement']['status'], row['settlement']['outstanding_amount']), ('CLOSED', '0.00'))

    def test_partial_repayment_overlay_stays_separate_from_original_classification(self):
        data, registry = resolved_snapshot({'KOTAK': [origin()], 'SBI': [receipt('30.00',
            'Alice repaid part for KOTAK_0001; as of 2026-08-02, outstanding balance is 70')]})
        identifier, _, _ = self.import_snapshot(data, registry)
        obligation, = self.view(identifier, 'obligations')['records']
        self.assertEqual((obligation['settlement']['status'], obligation['settlement']['outstanding_amount']), ('PARTIAL', '70.00'))
        transaction, = self.view(identifier, 'transactions', bank='sbi')['records']
        self.assertEqual(transaction['settlement_effect'], data['settlements']['classification_effects'][0])
        self.assertEqual(transaction['classification'], next(row for row in data['classification']['transactions']
                         if row['transaction_id'] == 'SBI_0001'))

    def test_relationship_and_settlement_views_keep_original_data(self):
        identifier, original, _ = self.import_snapshot()
        relationships = self.view(identifier, 'relationships')
        self.assertEqual({row['legacy_id']: row['data'] for row in relationships['records']},
                         {row['relationship_id']: row for row in original['relationships']['relationships']})
        settlements = self.view(identifier, 'settlements', status='CLOSED')
        self.assertEqual([row['data'] for row in settlements['records']], original['settlements']['obligation_settlement_view'])
        self.assertTrue(all(row['provenance'] for row in relationships['records'] + settlements['records']))

    def test_historical_status_snapshot_keeps_null_instead_of_latest_zero(self):
        from src import db_read
        old, registry = resolved_snapshot({'KOTAK': [origin()], 'SBI': [dict()]})
        old_id, _, _ = self.import_snapshot(old, registry)
        old_registry = deepcopy(registry)
        current = snapshot({'KOTAK': [origin(), receipt()], 'SBI': [dict()]})
        registry = identities.migrate(current, registry)
        current_id, _, _ = self.import_snapshot(current, registry)
        old_row, = self.view(old_id, 'obligations')['records']
        current_row, = self.view(current_id, 'obligations')['records']
        self.assertEqual(old_row['record_id'], current_row['record_id'])
        self.assertEqual((old_row['settlement']['status'], old_row['settlement']['outstanding_amount']), ('UNASSESSED', None))
        self.assertEqual((current_row['settlement']['status'], current_row['settlement']['outstanding_amount']), ('CLOSED', '0.00'))
        self.assertEqual(db_read.read_snapshot(self.connection, old_id)['registry'], old_registry)
        self.assertEqual(len(db_read.list_snapshots(self.connection)), 2)

    def test_historical_allocation_revision_is_selected_once_per_snapshot(self):
        old, registry = resolved_snapshot()
        old_id, _, _ = self.import_snapshot(old, registry)
        current = snapshot({'KOTAK': [origin(), receipt(note='Alice repaid in full for KOTAK_0001; synthetic extra context'),
                                     dict(amount='25.00', date='2026-08-03')],
                            'SBI': [dict(amount='20.00', date='2026-08-03')]})
        registry = identities.migrate(current, registry)
        new_id, _, _ = self.import_snapshot(current, registry)
        old_row, = self.view(old_id, 'obligations')['records']
        new_row, = self.view(new_id, 'obligations')['records']
        old_allocation, = old_row['allocations']
        new_allocation, = new_row['allocations']
        self.assertEqual(old_allocation['record_id'], new_allocation['record_id'])
        self.assertNotEqual(old_allocation['revision_id'], new_allocation['revision_id'])
        self.assertEqual(old_allocation['data'], old['settlements']['allocations'][0])
        self.assertEqual(new_allocation['data'], current['settlements']['allocations'][0])
        old_settlement, = self.view(old_id, 'settlements')['records']
        new_settlement, = self.view(new_id, 'settlements')['records']
        self.assertEqual(old_settlement['revision_id'], new_settlement['revision_id'])
        self.assertEqual(new_settlement['data']['verified_repayments'], '100.00')

    def test_corrupted_typed_bank_rows_are_rejected_without_writes(self):
        from src import db_read
        identifier, _, _ = self.import_snapshot()
        self.connection.execute("UPDATE finlyst.bank_transactions SET raw_description='Synthetic corruption'")
        before = self.rows()
        with self.assertRaises(ValueError):
            db_read.read_snapshot(self.connection, identifier)
        self.assertEqual(before, self.rows())
        self.assertEqual(self.connection.info.transaction_status, TransactionStatus.IDLE)

    def test_changed_original_source_payload_is_rejected_without_repair(self):
        from src import db_read
        identifier, _, _ = self.import_snapshot()
        self.connection.execute('''UPDATE finlyst.source_documents SET payload = jsonb_set(
            payload,'{source_rows,0,note}',to_jsonb('Synthetic altered original meaning'::text))
            WHERE source_id IN (SELECT source_id FROM finlyst.snapshot_sources WHERE role='events:kotak')''')
        before = self.rows()
        with self.assertRaises(ValueError):
            db_read.read_snapshot(self.connection, identifier)
        self.assertEqual(before, self.rows())

    def test_missing_required_identity_evidence_is_rejected_without_writes(self):
        from src import db_read
        identifier, _, _ = self.import_snapshot()
        removed = self.connection.execute('''DELETE FROM finlyst.identity_keys WHERE (record_id,identity_key) IN
            (SELECT record_id,identity_key FROM finlyst.identity_keys ORDER BY record_id,identity_key LIMIT 1)''').rowcount
        self.assertEqual(removed, 1)
        before = self.rows()
        with self.assertRaises(ValueError):
            db_read.read_snapshot(self.connection, identifier)
        self.assertEqual(before, self.rows())

    def test_later_identity_evidence_does_not_replace_historical_snapshot_registry(self):
        from src import db_read
        identifier, original, registry = self.import_snapshot()
        self.connection.execute('''INSERT INTO finlyst.identity_keys (record_id,identity_key)
            SELECT record_id,%s FROM finlyst.bank_transactions ORDER BY record_id LIMIT 1''', ('f' * 64,))
        before = self.rows()
        result = db_read.read_snapshot(self.connection, identifier)
        self.assertEqual(result['snapshot'], original)
        self.assertEqual(result['registry'], registry)
        self.assertEqual(before, self.rows())

    def test_extra_snapshot_membership_is_rejected_without_writes(self):
        from src import db_read
        identifier, _, _ = self.import_snapshot()
        self.connection.execute('''INSERT INTO finlyst.snapshot_sources (snapshot_id,role,source_id)
            SELECT snapshot_id,'unsupported_source_role',source_id FROM finlyst.snapshot_sources LIMIT 1''')
        before = self.rows()
        with self.assertRaises(ValueError):
            db_read.read_snapshot(self.connection, identifier)
        self.assertEqual(before, self.rows())


if __name__ == '__main__':
    unittest.main()
