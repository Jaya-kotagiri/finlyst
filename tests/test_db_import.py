"""Importer regressions using synthetic data and an explicitly disposable database."""

from copy import deepcopy
from decimal import Decimal
import os
import re
import unittest
from unittest.mock import patch

from psycopg import sql

from src import db, identities
from tests.import_support import resolved_snapshot, snapshot
from tests.support import origin, receipt


def prepare(data=None, registry=None):
    from src import db_import
    if data is None:
        data, registry = resolved_snapshot()
    return db_import.prepare(data, registry)


class ImportPreparationTests(unittest.TestCase):
    def test_projection_is_deterministic_pure_and_does_not_connect(self):
        data, registry = resolved_snapshot()
        original = deepcopy((data, registry))
        with patch('src.db.connect') as connect:
            self.assertEqual(prepare(data, registry), prepare(data, registry))
            connect.assert_not_called()
        self.assertEqual((data, registry), original)

    def test_money_is_exact_and_unknowns_remain_null(self):
        plan = prepare()
        self.assertTrue(all(isinstance(row['amount'], Decimal) for row in plan['rows']['bank_transactions']))
        unknown = [row for row in plan['rows']['effective_classifications'] if row['treatment'] == 'unknown']
        self.assertTrue(unknown)
        self.assertTrue(all(row['income'] is None and row['expense'] is None for row in unknown))

    def test_malformed_bank_facts_are_rejected_before_connecting(self):
        data, registry = resolved_snapshot()
        data['banks']['kotak'][0]['amount'] = 100.0
        with patch('src.db.connect') as connect, self.assertRaises(ValueError):
            prepare(data, registry)
        connect.assert_not_called()

    def test_missing_existing_mapping_is_not_silently_repaired(self):
        data, registry = resolved_snapshot()
        key = next(key for key, row in registry['legacy_mappings'].items()
                   if row['kind'] == 'transaction' and registry['sources'][row['source_id']]['role'] == 'bank')
        del registry['legacy_mappings'][key]
        with self.assertRaises(ValueError):
            prepare(data, registry)

    def test_inconsistent_downstream_classification_is_rejected(self):
        data, registry = resolved_snapshot()
        data['classification']['transactions'][0]['effective']['treatment'] = 'expense'
        with self.assertRaises(ValueError):
            prepare(data, registry)


@unittest.skipUnless(os.environ.get('FINLYST_TEST_DB_NAME'),
                     'Set FINLYST_TEST_DB_NAME to an isolated finlyst_test_* database')
class ImportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        name = os.environ['FINLYST_TEST_DB_NAME']
        if not re.fullmatch(r'finlyst_test_[a-z0-9_]{1,40}', name):
            raise ValueError('Importer tests require an explicitly isolated finlyst_test_* database')
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

    def import_snapshot(self, data=None, registry=None, **kwargs):
        from src import db_import
        if data is None:
            data, registry = resolved_snapshot()
        return db_import.import_snapshot(self.connection, data, registry, **kwargs)

    def test_repeated_import_reuses_every_row_and_preserves_counts(self):
        data, registry = resolved_snapshot()
        plan = prepare(data, registry)
        first = self.import_snapshot(data, registry)
        before = self.rows()
        second = self.import_snapshot(data, registry)
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertGreater(first['inserted_rows'], 0)
        self.assertEqual(second['inserted_rows'], 0)
        self.assertEqual(before, self.rows())
        for table, rows in plan['rows'].items():
            with self.subTest(table=table):
                self.assertEqual(len(before[table]), len(rows))
        for table, expected in [('bank_transactions', 4), ('semantic_events', 2),
                                ('effective_classifications', 4), ('obligations', 1)]:
            self.assertEqual(len(before[table]), expected)

    def test_exact_amounts_and_foreign_links_reach_the_same_obligation(self):
        self.import_snapshot()
        row = self.connection.execute('''SELECT a.amount,t.amount,o.original_amount,
            s.status,s.outstanding_amount,a.evidence_strength,a.confirmation_method
            FROM finlyst.allocations a
            JOIN finlyst.bank_transactions t ON t.record_id=a.repayment_transaction_id
            JOIN finlyst.obligations o ON o.record_id=a.obligation_id
            JOIN finlyst.settlements s ON s.obligation_id=o.record_id''').fetchone()
        self.assertEqual(row[:6], (Decimal('100.00'), Decimal('100.00'), Decimal('100.00'),
                                  'CLOSED', Decimal('0.00'), 'high'))
        self.assertEqual(row[6], 'source_supported')

    def test_cross_account_reimbursement_and_exact_source_evidence_survive_import(self):
        data, registry = resolved_snapshot({'KOTAK': [origin()],
                                            'SBI': [receipt(note='Alice sent her share')]})
        result = self.import_snapshot(data, registry)
        self.assertEqual(self.connection.execute('''SELECT origin.bank,repayment.bank,a.amount
            FROM finlyst.allocations a
            JOIN finlyst.bank_transactions repayment ON repayment.record_id=a.repayment_transaction_id
            JOIN finlyst.obligations o ON o.record_id=a.obligation_id
            JOIN finlyst.obligation_links link ON link.obligation_revision_id=o.revision_id AND link.role='origin'
            JOIN finlyst.bank_transactions origin ON origin.record_id=link.target_record_id''').fetchone(),
            ('KOTAK', 'SBI', Decimal('100.00')))
        source = identities.source_id('settlements', data['settlements'])
        self.assertEqual(self.connection.execute('SELECT payload FROM finlyst.source_documents WHERE source_id=%s',
                                                (source,)).fetchone()[0], data['settlements'])
        mapping = identities.observation_id(source, 'allocation', data['settlements']['allocations'][0]['allocation_id'])
        self.assertEqual(self.connection.execute('SELECT payload FROM finlyst.source_observations WHERE mapping_id=%s',
                                                (mapping,)).fetchone()[0], data['settlements']['allocations'][0])
        self.assertEqual(self.connection.execute('SELECT registry_payload FROM finlyst.imported_snapshots WHERE snapshot_id=%s',
                                                (result['snapshot_id'],)).fetchone()[0], registry)

    def test_unknown_principal_is_unassessed_not_zero(self):
        item = origin()
        item['note'] = 'Alice share, will send'
        item['components'][0]['amount'] = None
        data, registry = resolved_snapshot({'KOTAK': [item], 'SBI': [dict()]})
        self.import_snapshot(data, registry)
        self.assertEqual(self.connection.execute('''SELECT original_principal,outstanding_amount,status
            FROM finlyst.settlements''').fetchone(), (None, None, 'UNASSESSED'))

    def test_immediate_dad_return_preserves_inferred_confirmation(self):
        data, registry = resolved_snapshot({'KOTAK': [dict()], 'SBI': [
            dict(amount='3000.00', note='Dad will send it back right away, he needed it temporarily.',
                 narration='UPI/DR/123456789012/Dad/ICIC/dad@bank/UPI'),
            dict(amount='3000.00', direction='credit',
                 narration='UPI/CR/223456789012/Dad/ICIC/dad@bank/UPI')]},
            {'SBI': [dict(payee='Dad')]})
        self.import_snapshot(data, registry)
        self.assertEqual(self.connection.execute('SELECT evidence_strength,confirmation_method FROM finlyst.allocations').fetchone(),
                         ('high', 'inferred'))
        self.assertEqual(self.connection.execute('SELECT status FROM finlyst.settlements').fetchone(), ('CLOSED',))

    def test_before_commit_failure_rolls_back_every_table(self):
        before = self.rows()
        def fail():
            raise ValueError('Synthetic protected source changed')
        with self.assertRaisesRegex(ValueError, 'Synthetic protected source changed'):
            self.import_snapshot(before_commit=fail)
        self.assertEqual(before, self.rows())

    def test_late_database_failure_rolls_back_every_table(self):
        from src import db_import
        original = db_import._insert_or_compare
        visited = []
        def failing(*args, **kwargs):
            if 'allocations' in [value for value in args if isinstance(value, str)]:
                raise RuntimeError('Synthetic late write failure')
            visited.append(True)
            return original(*args, **kwargs)
        before = self.rows()
        with patch('src.db_import._insert_or_compare', side_effect=failing):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic late write failure'):
                self.import_snapshot()
        self.assertTrue(visited)
        self.assertEqual(before, self.rows())

    def test_conflicting_existing_row_is_never_overwritten(self):
        self.import_snapshot()
        self.connection.execute("UPDATE finlyst.bank_transactions SET raw_description='Synthetic conflicting database value'")
        before = self.rows()
        with self.assertRaises(ValueError):
            self.import_snapshot()
        self.assertEqual(before, self.rows())

    def test_unsupported_foreign_key_valid_relationship_edge_is_rejected(self):
        self.import_snapshot()
        inserted = self.connection.execute('''INSERT INTO finlyst.relationship_transactions
            (relationship_revision_id,transaction_id,transaction_kind,role)
            SELECT r.revision_id,t.record_id,'transaction','participant'
            FROM finlyst.relationships r CROSS JOIN finlyst.bank_transactions t
            WHERE NOT EXISTS (SELECT 1 FROM finlyst.relationship_transactions link
                WHERE link.relationship_revision_id=r.revision_id
                AND link.transaction_id=t.record_id AND link.role='participant') LIMIT 1''').rowcount
        self.assertEqual(inserted, 1)
        before = self.rows()
        with self.assertRaisesRegex(ValueError, 'relationship_transactions'):
            self.import_snapshot()
        self.assertEqual(before, self.rows())

    def test_extraneous_snapshot_source_membership_is_rejected(self):
        self.import_snapshot()
        self.connection.execute('''INSERT INTO finlyst.snapshot_sources (snapshot_id,role,source_id)
            SELECT snapshot_id,'synthetic_unsupported_role',source_id
            FROM finlyst.snapshot_sources LIMIT 1''')
        before = self.rows()
        with self.assertRaisesRegex(ValueError, 'snapshot_sources'):
            self.import_snapshot()
        self.assertEqual(before, self.rows())

    def test_pending_caller_transaction_is_not_committed(self):
        with self.connection.transaction():
            with self.assertRaisesRegex(ValueError, 'idle'):
                self.import_snapshot()

    def test_malformed_sources_write_nothing(self):
        data, registry = resolved_snapshot()
        data['settlements']['obligation_settlement_view'][0]['status'] = 'INVENTED'
        before = self.rows()
        with self.assertRaises(ValueError):
            self.import_snapshot(data, registry)
        self.assertEqual(before, self.rows())

    def test_counterparty_correction_preserves_old_revision_and_identity(self):
        old, registry = resolved_snapshot()
        self.import_snapshot(old, registry)
        current = snapshot({'KOTAK': [origin(party='Alicia'), receipt(note='Alicia repaid in full for KOTAK_0001'),
                                     dict(amount='25.00', date='2026-08-03')],
                            'SBI': [dict(amount='20.00', date='2026-08-03')]})
        old_origin = old['origins']['obligations'][0]
        target = identities.lookup(registry, identities.source_id('origins', old['origins']),
                                   'obligation', old_origin['obligation_id'])['durable_id']
        new_origin = current['origins']['obligations'][0]
        key = identities.observation_id(identities.source_id('origins', current['origins']),
                                        'obligation', new_origin['obligation_id'])
        registry = identities.migrate(current, registry, continuity={key: dict(target_id=target,
            actor='synthetic reviewer', reason='The original claimant corrected their name.')})
        self.import_snapshot(current, registry)
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT record_id),count(*) FROM finlyst.obligations').fetchone(), (1, 2))
        self.assertEqual({r[0] for r in self.connection.execute('SELECT counterparty FROM finlyst.obligations')}, {'Alice', 'Alicia'})
        self.assertEqual(self.connection.execute('SELECT count(*) FROM finlyst.imported_snapshots').fetchone(), (2,))

    def test_settlement_status_change_keeps_unassessed_history_and_stable_identity(self):
        old, registry = resolved_snapshot({'KOTAK': [origin()], 'SBI': [dict()]})
        self.import_snapshot(old, registry)
        current = snapshot({'KOTAK': [origin(), receipt()], 'SBI': [dict()]})
        registry = identities.migrate(current, registry)
        self.import_snapshot(current, registry)
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT record_id),count(*) FROM finlyst.settlements').fetchone(), (1, 2))
        self.assertEqual(set(self.connection.execute('SELECT status,outstanding_amount FROM finlyst.settlements').fetchall()),
                         {('UNASSESSED', None), ('CLOSED', Decimal('0.00'))})

    def test_overlapping_import_preserves_scoped_legacy_ids_without_duplicate_movements(self):
        expense = dict(origin(), date='2026-08-02', narration='Original synthetic expense')
        payment = dict(receipt(date='2026-08-03'), narration='Original synthetic repayment')
        old, registry = resolved_snapshot({'KOTAK': [expense, payment], 'SBI': [dict()]})
        self.import_snapshot(old, registry)
        prefix = dict(date='2026-07-31', amount='5.00', narration='Earlier synthetic transaction')
        current = snapshot({'KOTAK': [prefix, expense, dict(payment, note='Alice repaid in full for KOTAK_0002')],
                            'SBI': [dict()]}, opening_balances={'KOTAK': '10005.00'})
        registry = identities.migrate(current, registry)
        self.import_snapshot(current, registry)
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT record_id),count(*) FROM finlyst.bank_transactions').fetchone(), (4, 4))
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT record_id) FROM finlyst.obligations').fetchone(), (1,))

    def test_allocation_evidence_revision_retains_both_historical_settlement_links(self):
        old, registry = resolved_snapshot()
        self.import_snapshot(old, registry)
        current = snapshot({'KOTAK': [origin(), receipt(note='Alice repaid in full for KOTAK_0001; synthetic extra context'),
                                     dict(amount='25.00', date='2026-08-03')],
                            'SBI': [dict(amount='20.00', date='2026-08-03')]})
        registry = identities.migrate(current, registry)
        self.import_snapshot(current, registry)
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT record_id),count(*) FROM finlyst.allocations').fetchone(), (1, 2))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM finlyst.settlements').fetchone(), (1,))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM finlyst.settlement_allocations').fetchone(), (2,))


if __name__ == '__main__':
    unittest.main()
