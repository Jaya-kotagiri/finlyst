"""Pure database projections preserve typed facts and existing durable identities."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import unittest
from uuid import UUID

from src.db_projection import project
from src.identities import lookup, observation_id, source_id
from tests.import_support import resolved_snapshot
from tests.support import share


def sources(snapshot):
    return {
        **{'bank:' + bank: source_id('bank', rows) for bank, rows in snapshot['banks'].items()},
        **{'events:' + bank: source_id('events', document) for bank, document in snapshot['events'].items()},
        **{name: source_id(name, snapshot[name]) for name in
           ('relationships', 'classification', 'coverage', 'origins', 'settlements')},
    }


class ProjectionTests(unittest.TestCase):
    def test_exact_bank_facts_shared_accounts_and_repeatability(self):
        snapshot, registry = resolved_snapshot()
        before = deepcopy((snapshot, registry))
        source_ids = sources(snapshot)
        result = project(snapshot, registry, source_ids)
        self.assertEqual(project(snapshot, registry, source_ids), result)
        self.assertEqual((snapshot, registry), before)
        self.assertEqual(len(result['accounts']), 2)
        self.assertEqual(len(result['bank_transactions']), 4)
        for original in snapshot['banks']['kotak']:
            decision = lookup(registry, source_ids['bank:kotak'], 'transaction', original['transaction_id'])
            row = next(r for r in result['bank_transactions'] if r['record_id'] == UUID(decision['durable_id']))
            self.assertEqual(row['revision_id'], UUID(decision['revision_id']))
            self.assertEqual(row['amount'], Decimal(original['amount']))
            self.assertIsInstance(row['amount'], Decimal)
            self.assertEqual(row['balance_after'], Decimal(original['balance_after']))
            self.assertEqual(row['transaction_date'], date.fromisoformat(original['transaction_date']))
            self.assertEqual(row['raw_description'], original['raw_description'])
            self.assertEqual(row['reference'], original['reference'])

    def test_null_unknowns_and_unassessed_are_not_converted_to_zero(self):
        snapshot, registry = resolved_snapshot({'KOTAK': [dict(amount='200.00',
            note='Alice share 40, Bob will send his share later', components=[share(), share('Bob', None)]),
            dict(amount='5.00', date='2026-08-02')]})
        result = project(snapshot, registry, sources(snapshot))
        unknown = next(r for r in result['effective_classifications'] if r['treatment'] == 'unknown')
        self.assertIsNone(unknown['income'])
        self.assertIsNone(unknown['expense'])
        self.assertIsNone(unknown['semantic'])
        bob = next(r for r in result['obligations'] if r['counterparty'] == 'Bob')
        self.assertIsNone(bob['original_amount'])
        settlement = next(r for r in result['settlements'] if r['obligation_id'] == bob['record_id'])
        self.assertEqual(settlement['status'], 'UNASSESSED')
        self.assertIsNone(settlement['outstanding_amount'])
        self.assertIsNone(settlement['original_principal'])
        self.assertIsNone(result['semantic_events'][0]['description'])

    def test_allocation_and_settlement_links_use_durable_ids(self):
        snapshot, registry = resolved_snapshot()
        result = project(snapshot, registry, sources(snapshot))
        allocation, = result['allocations']
        settlement, = result['settlements']
        obligation, = result['obligations']
        edge, = result['settlement_allocations']
        self.assertEqual(allocation['obligation_id'], obligation['record_id'])
        self.assertEqual(settlement['obligation_id'], obligation['record_id'])
        self.assertEqual(edge['allocation_record_id'], allocation['record_id'])
        self.assertEqual(edge['allocation_revision_id'], allocation['revision_id'])
        self.assertEqual(edge['settlement_revision_id'], settlement['revision_id'])
        self.assertIn(allocation['repayment_transaction_id'],
                      {r['record_id'] for r in result['bank_transactions']})
        self.assertEqual(allocation['amount'], Decimal('100.00'))
        self.assertEqual(settlement['outstanding_amount'], Decimal('0.00'))
        self.assertEqual(allocation['confirmation_method'],
                         snapshot['settlements']['allocations'][0]['confirmation_method'])

    def test_changed_source_payload_cannot_borrow_existing_identity(self):
        snapshot, registry = resolved_snapshot()
        source_ids = sources(snapshot)
        snapshot['banks']['kotak'][0]['amount'] = '101.00'
        with self.assertRaisesRegex(ValueError, 'source payload differs'):
            project(snapshot, registry, source_ids)

    def test_unresolved_mapping_is_rejected_without_guessing(self):
        snapshot, registry = resolved_snapshot()
        source_ids = sources(snapshot)
        key = observation_id(source_ids['bank:kotak'], 'transaction', 'KOTAK_0001')
        decision = registry['legacy_mappings'][key]['decisions'][-1]
        decision.update(status='unresolved', durable_id=None, revision_id=None)
        with self.assertRaisesRegex(ValueError, 'no resolved durable identity'):
            project(snapshot, registry, source_ids)


if __name__ == '__main__':
    unittest.main()
