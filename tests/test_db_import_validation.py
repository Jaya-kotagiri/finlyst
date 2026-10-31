"""Synthetic checks against internally valid but source-inconsistent registries."""

from copy import deepcopy
import unittest

from src import identities
from src.db_import_validation import validate_current_mappings
from tests.import_support import resolved_snapshot


def source_ids(snapshot):
    result = {}
    for bank, rows in snapshot['banks'].items():
        result['bank:' + bank] = identities.source_id('bank', rows)
    for bank, document in snapshot['events'].items():
        result['events:' + bank] = identities.source_id('events', document)
    for document in snapshot['reconciled']:
        result['reconciled:' + document['bank'].lower()] = identities.source_id('reconciled', document)
    for role in ('relationships', 'classification', 'coverage', 'origins', 'settlements'):
        result[role] = identities.source_id(role, snapshot[role])
    return result


def replace_revision(registry, revision_id, **changes):
    """Keep ID hashes and alias references internally consistent after corruption."""
    revision = dict(registry['revisions'].pop(revision_id), **changes)
    changed_id = identities.identifier('revision', revision)
    registry['revisions'][changed_id] = revision
    for mapping in registry['legacy_mappings'].values():
        for decision in mapping['decisions']:
            if decision['revision_id'] == revision_id:
                decision['revision_id'] = changed_id
                decision['references'] = deepcopy(revision['references'])
    identities.validate_registry(registry)


class CurrentMappingValidationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.registry = resolved_snapshot()
        self.sources = source_ids(self.snapshot)

    def test_valid_snapshot_preserves_every_input(self):
        before = deepcopy((self.snapshot, self.registry, self.sources))
        self.assertIsNone(validate_current_mappings(self.snapshot, self.registry, self.sources))
        self.assertEqual((self.snapshot, self.registry, self.sources), before)

    def test_self_consistent_false_interpretation_hash_is_rejected_for_each_kind(self):
        kinds = {'account', 'transaction', 'event', 'relationship', 'obligation', 'allocation', 'settlement'}
        for kind in kinds:
            with self.subTest(kind=kind):
                registry = deepcopy(self.registry)
                record = next(i for i, r in registry['records'].items() if r['kind'] == kind)
                revision = next(i for i, r in registry['revisions'].items() if r['record_id'] == record)
                replace_revision(registry, revision, interpretation_sha256='0' * 64)
                # The existing identity stage trusts saved resolved observations.
                self.assertEqual(identities.migrate(self.snapshot, registry), registry)
                with self.assertRaisesRegex(ValueError, 'interpretation hash'):
                    validate_current_mappings(self.snapshot, registry, self.sources)

    def test_internally_consistent_event_link_to_wrong_bank_record_is_rejected(self):
        revision = next(i for i, r in self.registry['revisions'].items()
                        if self.registry['records'][r['record_id']]['kind'] == 'event')
        old = self.registry['revisions'][revision]['references']['transaction'][0]
        other = next(i for i, r in self.registry['records'].items() if r['kind'] == 'transaction' and i != old)
        replace_revision(self.registry, revision, references={'transaction': [other]})
        self.assertEqual(identities.migrate(self.snapshot, self.registry), self.registry)
        with self.assertRaisesRegex(ValueError, 'durable references differ'):
            validate_current_mappings(self.snapshot, self.registry, self.sources)

    def test_alias_cannot_point_to_another_valid_primary_record(self):
        mapping = next(m for m in self.registry['legacy_mappings'].values()
                       if m['source_id'] == self.sources['classification'])
        other = next(m for m in self.registry['legacy_mappings'].values()
                     if m['source_id'] == self.sources['bank:kotak'] and m['kind'] == 'transaction'
                     and m['legacy_id'] != mapping['legacy_id'])
        mapping['decisions'][-1] = deepcopy(other['decisions'][-1])
        identities.validate_registry(self.registry)
        with self.assertRaisesRegex(ValueError, 'projection alias differs'):
            validate_current_mappings(self.snapshot, self.registry, self.sources)

    def test_projection_observation_hash_is_checked_separately_from_revision(self):
        mapping = next(m for m in self.registry['legacy_mappings'].values()
                       if m['source_id'] == self.sources['coverage'])
        mapping['record_sha256'] = '0' * 64
        identities.validate_registry(self.registry)
        with self.assertRaisesRegex(ValueError, 'observation hash/mapping mismatch'):
            validate_current_mappings(self.snapshot, self.registry, self.sources)

    def test_extra_current_source_observation_is_not_imported_as_another_record(self):
        mapping = deepcopy(next(m for m in self.registry['legacy_mappings'].values()
                                if m['source_id'] == self.sources['coverage']))
        mapping['legacy_id'] = 'KOTAK_9999'
        oid = identities.observation_id(mapping['source_id'], mapping['kind'], mapping['legacy_id'])
        self.registry['legacy_mappings'][oid] = mapping
        identities.validate_registry(self.registry)
        with self.assertRaisesRegex(ValueError, 'observation coverage'):
            validate_current_mappings(self.snapshot, self.registry, self.sources)

    def test_unresolved_identity_is_reported_without_dropping_its_source_row(self):
        mapping = next(m for m in self.registry['legacy_mappings'].values()
                       if m['source_id'] == self.sources['bank:kotak'] and m['kind'] == 'transaction')
        decision = mapping['decisions'][-1]
        decision.update(status='unresolved', durable_id=None, revision_id=None)
        identities.validate_registry(self.registry)
        with self.assertRaisesRegex(ValueError, 'identity requires resolution'):
            validate_current_mappings(self.snapshot, self.registry, self.sources)


if __name__ == '__main__':
    unittest.main()
