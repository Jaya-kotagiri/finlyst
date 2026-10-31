"""HTTP/SQL parity using disposable synthetic databases, never private fixtures."""

import os
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from psycopg import sql

from src import api, db, db_import, db_read, identities
from tests.import_support import resolved_snapshot, snapshot
from tests.support import origin, receipt


@unittest.skipUnless(os.environ.get('FINLYST_TEST_DB_NAME'), 'Requires isolated finlyst_test_* database')
class DatabaseApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        name = os.environ['FINLYST_TEST_DB_NAME']
        if not re.fullmatch(r'finlyst_test_[a-z0-9_]{1,40}', name):
            raise ValueError('API tests require an isolated finlyst_test_* database')
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
        self.enterContext(patch.dict(os.environ, {'FINLYST_DB_NAME': self.env['FINLYST_DB_NAME']}))
        self.client = self.enterContext(TestClient(api.create_app(), base_url='http://127.0.0.1',
                                                   client=('127.0.0.1', 50000)))

    def clear(self):
        name = self.connection.execute('SELECT current_database()').fetchone()[0]
        if name != self.env['FINLYST_DB_NAME'] or not re.fullmatch(r'finlyst_test_[a-z0-9_]{1,40}', name):
            raise ValueError('Refusing to clear a non-disposable database')
        self.connection.execute(sql.SQL('TRUNCATE TABLE {}').format(sql.SQL(',').join(
            sql.Identifier('finlyst', table) for table in self.tables)))

    def rows(self):
        return {table: sorted(self.connection.execute(sql.SQL('SELECT * FROM finlyst.{}').format(
            sql.Identifier(table))).fetchall(), key=repr) for table in self.tables}

    def seed(self, data=None, registry=None):
        if data is None:
            data, registry = resolved_snapshot()
        return db_import.import_snapshot(self.connection, data, registry)['snapshot_id']

    def get(self, view, identifier, **filters):
        response = self.client.get('/api/v1/' + view, params=dict(snapshot_id=identifier, **filters))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['snapshot_id'], identifier)
        return response.json()

    def test_all_views_summaries_details_and_repeated_reads_match_sql_without_writes(self):
        identifier = self.seed()
        before = self.rows()
        for _ in range(2):
            self.assertEqual(self.client.get('/api/v1/snapshots').json()['data'],
                             db_read.list_snapshots(self.connection))
            for view in db_read.VIEWS:
                self.assertEqual(self.get(view, identifier)['data'],
                                 db_read.read_view(self.connection, identifier, view)['records'])
            summary = self.get('summaries', identifier)['data']
            self.assertEqual(summary, db_read.read_summary(self.connection, identifier))
            self.assertFalse(summary['financial']['income_expense_complete'])
            self.assertEqual(summary['financial']['verified_principal_repayments_included_in_excluded'], '100.00')
            row = self.get('transactions', identifier)['data'][0]
            self.assertEqual(self.get('transactions/' + row['record_id'], identifier)['data'], row)
        self.assertEqual(before, self.rows())

    def test_filters_pagination_and_empty_selections_match_sql(self):
        identifier = self.seed()
        account = self.get('accounts', identifier, bank='kotak')['data'][0]['record_id']
        filters = [dict(bank='kotak'), dict(account_id=account), dict(date_from='2026-08-02', date_to='2026-08-03'),
                   dict(treatment='unknown'), dict(category='not present'), dict(uncategorized=True)]
        for selection in filters:
            with self.subTest(selection=selection):
                expected = db_read.read_view(self.connection, identifier, 'transactions', **selection)['records']
                result = self.get('transactions', identifier, limit=1, offset=1, **selection)
                self.assertEqual(result['data'], expected[1:2])
                self.assertEqual(result['pagination']['total'], len(expected))
                self.assertEqual(self.get('summaries', identifier, **selection)['data'],
                                 db_read.read_summary(self.connection, identifier, **selection))
        empty = self.get('transactions', identifier, date_from='2030-01-01')
        self.assertEqual(empty['data'], [])
        self.assertEqual(empty['pagination']['total'], 0)
        self.assertIsNone(empty['pagination']['next_offset'])

    def test_null_balances_principals_and_unassessed_outstanding_remain_null(self):
        item = origin()
        item.update(note='Alice share; she will send', reference='123456789012')
        item['components'][0]['amount'] = None
        data = snapshot({'KOTAK': [item], 'SBI': [dict(reference='223456789012')]}, unknown_balances=True)
        identifier = self.seed(data, identities.migrate(data))
        self.assertTrue(all(row['balance_after'] is None for row in self.get('accounts', identifier)['data']))
        row, = self.get('obligations', identifier, status='UNASSESSED')['data']
        self.assertIsNone(row['obligation']['original_amount'])
        self.assertIsNone(row['settlement']['outstanding_amount'])
        self.assertEqual(self.get('obligations', identifier, status='CLOSED')['data'], [])
        summary = self.get('summaries', identifier)['data']['obligations']['receivable']
        self.assertIsNone(summary['total_outstanding'])
        self.assertEqual(summary['unknown_principal_count'], 1)

    def test_dad_closure_preserves_inferred_high_evidence_and_sources(self):
        data, registry = resolved_snapshot({'KOTAK': [dict()], 'SBI': [
            dict(amount='3000.00', note='Dad will send it back right away, he needed it temporarily.',
                 narration='UPI/DR/123456789012/Dad/ICIC/dad@bank/UPI'),
            dict(amount='3000.00', direction='credit', narration='UPI/CR/223456789012/Dad/ICIC/dad@bank/UPI')]},
            {'SBI': [dict(payee='Dad')]})
        identifier = self.seed(data, registry)
        row, = self.get('obligations', identifier, status='CLOSED')['data']
        allocation, = row['allocations']
        self.assertEqual((allocation['data']['evidence_strength'], allocation['data']['confirmation_method']),
                         ('high', 'inferred'))
        self.assertEqual(row['settlement']['outstanding_amount'], '0.00')
        self.assertTrue(row['provenance'])
        self.assertEqual(self.get('obligations', identifier)['data'],
                         db_read.read_view(self.connection, identifier, 'obligations')['records'])

    def test_partial_repayment_keeps_original_classification_and_overlay_separate(self):
        data, registry = resolved_snapshot({'KOTAK': [origin()], 'SBI': [receipt('30.00',
            'Alice repaid part for KOTAK_0001; as of 2026-08-02, outstanding balance is 70')]})
        identifier = self.seed(data, registry)
        row, = self.get('obligations', identifier, status='PARTIAL')['data']
        self.assertEqual(row['settlement']['outstanding_amount'], '70.00')
        transaction, = self.get('transactions', identifier, bank='sbi')['data']
        self.assertEqual(transaction['settlement_effect'], data['settlements']['classification_effects'][0])
        self.assertEqual(transaction['classification'], next(row for row in data['classification']['transactions']
                         if row['transaction_id'] == 'SBI_0001'))

    def test_empty_database_missing_snapshot_and_missing_transaction_are_clear(self):
        self.assertEqual(self.client.get('/api/v1/snapshots').json()['data'], [])
        identifier = '11111111-1111-5111-8111-111111111111'
        response = self.client.get('/api/v1/transactions', params={'snapshot_id': identifier})
        self.assertEqual((response.status_code, response.json()['error']['code']), (404, 'snapshot_not_found'))
        snapshot_id = self.seed()
        response = self.client.get('/api/v1/transactions/' + identifier, params={'snapshot_id': snapshot_id})
        self.assertEqual((response.status_code, response.json()['error']['code']), (404, 'transaction_not_found'))

    def test_inconsistent_database_is_rejected_without_exposing_or_repairing_it(self):
        identifier = self.seed()
        self.connection.execute("UPDATE finlyst.bank_transactions SET raw_description='private corrupt evidence'")
        before = self.rows()
        response = self.client.get('/api/v1/transactions', params={'snapshot_id': identifier})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'data_integrity_error')
        self.assertNotIn('private corrupt evidence', response.text)
        self.assertEqual(before, self.rows())
