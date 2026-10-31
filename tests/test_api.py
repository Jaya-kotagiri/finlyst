"""HTTP contracts and local access safeguards using synthetic query results."""

from copy import deepcopy
import os
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from src import api, db_read, identities
from tests.import_support import resolved_snapshot


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data, registry = resolved_snapshot()
        cls.sid = '11111111-1111-5111-8111-111111111111'
        cls.bundle = dict(snapshot_id=cls.sid, snapshot=data, registry=registry,
            sources={role: identities.source_id(role.split(':')[0], doc) for role, doc in api.db_read.db_import.documents(data).items()})

    def setUp(self):
        self.app = api.create_app()
        self.client = TestClient(self.app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000))
        self.addCleanup(self.client.close)

    def fake_query(self, request, operation, *args, **kwargs):
        if operation is db_read.list_snapshots:
            return []
        self.assertIs(operation, db_read.read_view)
        self.assertEqual(str(args[0]), self.sid)
        return db_read._view(self.bundle, args[1], **kwargs)

    def test_health_and_automatic_docs_do_not_connect(self):
        with patch('src.api.db.connect') as connect:
            self.assertEqual(self.client.get('/health').json()['data']['mode'], 'local-read-only')
            self.assertEqual(self.client.get('/docs').status_code, 200)
            spec = self.client.get('/openapi.json').json()
            self.assertIn('/api/v1/transactions', spec['paths'])
            self.assertIn('TransactionRecord', spec['components']['schemas'])
            self.assertTrue(all(set(path) <= {'get'} for path in spec['paths'].values()))
        connect.assert_not_called()

    def test_typed_lists_preserve_facts_and_provenance(self):
        with patch('src.api.query', side_effect=self.fake_query):
            for view in ('accounts', 'transactions', 'categories', 'relationships', 'obligations', 'settlements'):
                with self.subTest(view=view):
                    response = self.client.get('/api/v1/' + view, params={'snapshot_id': self.sid})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['data'], db_read._view(self.bundle, view)['records'])
                    self.assertEqual(response.json()['api_version'], '4A.1')
                    self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_pagination_is_stable_filtered_and_bounded(self):
        with patch('src.api.query', side_effect=self.fake_query):
            first = self.client.get('/api/v1/transactions', params={'snapshot_id': self.sid, 'bank': 'kotak', 'limit': 2}).json()
            self.assertEqual(first['pagination'], dict(total=3, offset=0, limit=2, returned=2, next_offset=2))
            second = self.client.get('/api/v1/transactions', params={'snapshot_id': self.sid, 'bank': 'kotak', 'limit': 2, 'offset': 2}).json()
            self.assertEqual(second['pagination']['returned'], 1)
            self.assertIsNone(second['pagination']['next_offset'])
            self.assertEqual(len({row['record_id'] for row in first['data'] + second['data']}), 3)
            empty = self.client.get('/api/v1/transactions', params={'snapshot_id': self.sid, 'offset': 100}).json()
            self.assertEqual(empty['data'], [])
            self.assertEqual(empty['pagination']['total'], 4)

    def test_detail_and_missing_record(self):
        row = db_read._view(self.bundle, 'transactions')['records'][0]
        with patch('src.api.query', side_effect=self.fake_query):
            response = self.client.get('/api/v1/transactions/' + row['record_id'], params={'snapshot_id': self.sid})
            self.assertEqual(response.json()['data'], row)
            response = self.client.get('/api/v1/transactions/' + str(UUID(int=0)), params={'snapshot_id': self.sid})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()['error']['code'], 'transaction_not_found')

    def test_bad_filters_and_pagination_fail_before_querying(self):
        cases = [{}, {'snapshot_id': 'invalid-secret'}, {'limit': 0}, {'limit': 101}, {'offset': -1},
            {'date_from': '2026-02-30'}, {'date_from': '2026-09-01', 'date_to': '2026-08-01'},
            {'category': 'Food', 'uncategorized': True}, {'treatment': 'invented'}, {'account_id': 'invalid'},
            {'unexpected': 'private-secret'}]
        with patch('src.api.query') as query:
            for case in cases:
                params = dict(snapshot_id=self.sid, **case) if 'snapshot_id' not in case else case
                if not case:
                    params = {}
                with self.subTest(case=case):
                    response = self.client.get('/api/v1/transactions', params=params)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()['error']['code'], 'invalid_request')
                    self.assertNotIn('private-secret', response.text)
                    self.assertNotIn('invalid-secret', response.text)
        query.assert_not_called()

    def test_host_origin_and_forwarded_headers_cannot_bypass_local_access(self):
        headers = [{'host': 'evil.example'}, {'origin': 'https://evil.example'}, {'origin': 'http://localhost'},
                   {'origin': 'null'}, {'x-forwarded-for': '127.0.0.1'}, {'forwarded': 'for=127.0.0.1'},
                   {'sec-fetch-site': 'cross-site'}, {'host': 'localhost:invalid'}]
        with patch('src.api.query') as query:
            for header in headers:
                with self.subTest(headers=header):
                    response = self.client.get('/api/v1/snapshots', headers=header)
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn('access-control-allow-origin', response.headers)
        query.assert_not_called()
        self.assertEqual(self.client.get('/health', headers={'origin': 'http://127.0.0.1'}).status_code, 200)

    def test_remote_peer_and_write_methods_are_rejected(self):
        with TestClient(self.app, base_url='http://127.0.0.1', client=('203.0.113.8', 50000)) as remote:
            self.assertEqual(remote.get('/health').status_code, 403)
        with patch('src.api.query') as query:
            for method in ('post', 'put', 'patch', 'delete'):
                response = getattr(self.client, method)('/api/v1/transactions')
                self.assertEqual(response.status_code, 405)
        query.assert_not_called()

    def test_database_and_integrity_errors_are_clear_and_redacted(self):
        with patch('src.api.db.connect', side_effect=ValueError('private-password')):
            response = self.client.get('/api/v1/snapshots')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private-password', response.text)
        for message, status in [('Snapshot was not found; synthetic', 404), ('private bank narration', 409),
                                ('Database query failed: OperationalError', 503)]:
            with patch('src.api.db.connect'), patch('src.api.db_read.read_view', side_effect=ValueError(message)):
                response = self.client.get('/api/v1/transactions', params={'snapshot_id': self.sid})
                self.assertEqual(response.status_code, status)
                self.assertNotIn('private bank narration', response.text)

    def test_invalid_response_never_returns_partial_or_untyped_financial_data(self):
        result = db_read._view(self.bundle, 'transactions')
        result = deepcopy(result)
        result['records'][0]['bank_transaction']['amount'] = 100.0
        with patch('src.api.query', return_value=result):
            response = self.client.get('/api/v1/transactions', params={'snapshot_id': self.sid})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['error']['code'], 'response_validation_error')
        self.assertNotIn('bank_transaction', response.text)

    def test_empty_snapshots_and_unknown_routes_use_consistent_json(self):
        with patch('src.api.query', return_value=[]):
            self.assertEqual(self.client.get('/api/v1/snapshots').json()['data'], [])
        response = self.client.get('/api/v1/not-an-endpoint')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['api_version'], '4A.1')

    def test_launcher_is_local_and_does_not_trust_proxies(self):
        with patch.dict(os.environ, {'FINLYST_API_HOST': '127.0.0.1', 'FINLYST_API_PORT': '8000'}), patch('uvicorn.run') as run:
            api.main()
        self.assertEqual(run.call_args.kwargs, dict(host='127.0.0.1', port=8000, proxy_headers=False, access_log=False, server_header=False))
        for values in ({'FINLYST_API_HOST': '0.0.0.0'}, {'FINLYST_API_HOST': '127.0.0.1', 'FINLYST_API_PORT': '0'}):
            with patch.dict(os.environ, values), patch('uvicorn.run') as run, self.assertRaises(ValueError):
                api.main()
            run.assert_not_called()
