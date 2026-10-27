"""Durable identity, overlap, continuity and reference tests using synthetic data."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from src import identities
from src.obligations import build as originate
from src.reconcile import reconcile, digest
from src.settlements import build as settle
from tests.support import doc, origin, prepared, receipt, share, temporary_directory, with_semantics


def rows(days=(1, 2, 3), account='1234567890', bank='KOTAK'):
    return [dict(transaction_id=f'{bank}_{i:04d}', bank=bank, account=account,
        transaction_date=f'2026-08-{day:02d}', amount='10.00', direction='debit',
        raw_description=f'Synthetic payment {day}', reference=f'REF-{day}',
        balance_after=f'{100 - day * 10}.00') for i, day in enumerate(days, 1)]


def snapshot(specs=None, semantics=None):
    document = doc('KOTAK', specs if specs is not None else [origin(), receipt()])
    if semantics:
        document = with_semantics(document, semantics)
    bank = [dict(r['bank_transaction'], account='1234567890') for r in document['transactions']]
    document = reconcile(bank, document['event_source_document'])
    data = prepared([document])
    return dict(banks={'kotak': bank}, events={'kotak': document['event_source_document']},
        reconciled=data[0], relationships=data[1], classification=data[2], coverage=data[3],
        origins=data[4], settlements=settle(*data))


def record_ids(state, kind):
    return {key for key, record in state['records'].items() if record['kind'] == kind}


def mapped(state, source, kind, legacy):
    return identities.lookup(state, source, kind, legacy)['durable_id']


class BankIdentityTests(unittest.TestCase):
    def test_repeated_import_is_idempotent_and_inputs_unchanged(self):
        initial = identities.empty_registry(); bank = rows(); before = deepcopy(bank)
        state, source = identities.import_bank(initial, bank)
        repeated, again = identities.import_bank(state, bank)
        self.assertEqual(repeated, state)
        self.assertEqual(source, again)
        self.assertEqual(bank, before)
        self.assertEqual(initial, identities.empty_registry())
        self.assertEqual(len(record_ids(state, 'transaction')), 3)

    def test_overlapping_period_reuses_transactions_despite_renumbering(self):
        state, first = identities.import_bank(identities.empty_registry(), rows((1, 2)))
        state, second = identities.import_bank(state, rows((2, 3)))
        self.assertEqual(mapped(state, first, 'transaction', 'KOTAK_0002'),
                         mapped(state, second, 'transaction', 'KOTAK_0001'))
        self.assertNotEqual(mapped(state, first, 'transaction', 'KOTAK_0001'),
                            mapped(state, second, 'transaction', 'KOTAK_0001'))
        self.assertEqual(len(record_ids(state, 'transaction')), 3)
        self.assertEqual(len(state['revisions']), 4)  # Three facts plus the account, not four cashflows.

    def test_overlap_in_reverse_import_order_has_same_bank_ids(self):
        a, _ = identities.import_bank(identities.empty_registry(), rows((1, 2)))
        a, _ = identities.import_bank(a, rows((2, 3)))
        b, _ = identities.import_bank(identities.empty_registry(), rows((2, 3)))
        b, _ = identities.import_bank(b, rows((1, 2)))
        self.assertEqual(record_ids(a, 'transaction'), record_ids(b, 'transaction'))

    def test_accounts_banks_and_months_do_not_collide(self):
        state = identities.empty_registry(); targets = []
        examples = [rows((1,)), rows((1,), account='9876543210'), rows((1,), bank='SBI'),
                    [dict(rows((1,))[0], transaction_date='2026-09-01')]]
        for bank in examples:
            state, source = identities.import_bank(state, bank)
            targets.append(mapped(state, source, 'transaction', bank[0]['transaction_id']))
        self.assertEqual(len(set(targets)), 4)
        self.assertEqual(len(record_ids(state, 'account')), 3)

    def test_renamed_or_reformatted_sources_only_add_provenance(self):
        bank = rows()
        state, one = identities.import_bank(identities.empty_registry(), bank,
            metadata={'location': 'first.csv', 'content_sha256': 'a' * 64})
        renamed, same = identities.import_bank(state, bank,
            metadata={'location': 'renamed.csv', 'content_sha256': 'a' * 64})
        other, two = identities.import_bank(renamed, bank,
            metadata={'location': 'reexport.csv', 'content_sha256': 'b' * 64})
        self.assertEqual(one, same)
        self.assertNotEqual(one, two)
        self.assertEqual(record_ids(state, 'transaction'), record_ids(other, 'transaction'))
        self.assertEqual(len(state['revisions']), len(other['revisions']))
        self.assertEqual(other['sources'][one]['locations'], ['first.csv', 'renamed.csv'])

    def test_missing_reference_can_match_exact_unique_balance_and_narration(self):
        first = [dict(r, reference=None) for r in rows((1, 2), bank='SBI')]
        second = [dict(r, reference=None) for r in rows((2, 3), bank='SBI')]
        state, one = identities.import_bank(identities.empty_registry(), first)
        state, two = identities.import_bank(state, second)
        self.assertEqual(mapped(state, one, 'transaction', 'SBI_0002'), mapped(state, two, 'transaction', 'SBI_0001'))
        self.assertEqual(len(record_ids(state, 'transaction')), 3)

    def test_missing_or_masked_account_remains_unresolved(self):
        for account in (None, '****1234', '1234'):
            with self.subTest(account=account):
                state, source = identities.import_bank(identities.empty_registry(), rows((1,), account=account))
                decision = identities.lookup(state, source, 'transaction', 'KOTAK_0001')
                self.assertEqual(decision['reason'], 'full_account_identity_required')
                self.assertIsNone(decision['durable_id'])
                self.assertFalse(state['records'])

    def test_amount_date_and_narration_without_reference_or_balance_are_insufficient(self):
        bank = [dict(rows((1,))[0], reference=None, balance_after=None)]
        state, source = identities.import_bank(identities.empty_registry(), bank)
        self.assertIsNone(mapped(state, source, 'transaction', 'KOTAK_0001'))
        self.assertFalse(record_ids(state, 'transaction'))

    def test_indistinguishable_occurrences_are_not_merged_or_dropped(self):
        bank = rows((1,)); bank.append(dict(bank[0], transaction_id='KOTAK_0002'))
        state, source = identities.import_bank(identities.empty_registry(), bank)
        for legacy in ('KOTAK_0001', 'KOTAK_0002'):
            decision = identities.lookup(state, source, 'transaction', legacy)
            self.assertEqual(decision['reason'], 'indistinguishable_bank_occurrences')
            self.assertIsNone(decision['durable_id'])
        self.assertFalse(record_ids(state, 'transaction'))
        self.assertEqual(identities.summary(state)['unresolved_mappings'], 2)

    def test_ambiguous_overlap_retains_existing_record_without_second_cashflow(self):
        bank = rows((1,)); state, old = identities.import_bank(identities.empty_registry(), bank)
        duplicate = bank + [dict(bank[0], transaction_id='KOTAK_0002')]
        state, new = identities.import_bank(state, duplicate)
        self.assertEqual(len(record_ids(state, 'transaction')), 1)
        self.assertIsNotNone(mapped(state, old, 'transaction', 'KOTAK_0001'))
        self.assertIsNone(mapped(state, new, 'transaction', 'KOTAK_0001'))
        self.assertTrue(identities.lookup(state, new, 'transaction', 'KOTAK_0001')['candidate_ids'])

    def test_a_subset_cannot_erase_prior_occurrence_ambiguity(self):
        bank = rows((1,)); duplicate = bank + [dict(bank[0], transaction_id='KOTAK_0002')]
        state, _ = identities.import_bank(identities.empty_registry(), duplicate)
        state, source = identities.import_bank(state, bank)
        self.assertIsNone(mapped(state, source, 'transaction', 'KOTAK_0001'))
        self.assertFalse(record_ids(state, 'transaction'))

    def test_confirmed_bank_correction_retains_id_and_adds_revision(self):
        bank = rows((1,)); state, original = identities.import_bank(identities.empty_registry(), bank)
        target = mapped(state, original, 'transaction', 'KOTAK_0001')
        corrected = [dict(bank[0], amount='11.00')]
        source = identities.source_id('bank', corrected)
        oid = identities.observation_id(source, 'transaction', 'KOTAK_0001')
        updated, _ = identities.import_bank(state, corrected, continuity={oid: dict(
            target_id=target, actor='synthetic reviewer', reason='Verified extraction correction from original bank evidence.')})
        self.assertEqual(mapped(updated, source, 'transaction', 'KOTAK_0001'), target)
        self.assertEqual(len(record_ids(updated, 'transaction')), 1)
        self.assertEqual(sum(r['record_id'] == target for r in updated['revisions'].values()), 2)

    def test_conflicting_bank_facts_are_not_silently_merged(self):
        bank = rows((1,)); state, _ = identities.import_bank(identities.empty_registry(), bank)
        bank[0]['amount'] = '11.00'
        state, source = identities.import_bank(state, bank)
        decision = identities.lookup(state, source, 'transaction', 'KOTAK_0001')
        self.assertEqual(decision['reason'], 'conflicting_or_incomplete_bank_identity_evidence')
        self.assertIsNone(decision['durable_id'])
        self.assertEqual(len(record_ids(state, 'transaction')), 1)

    def test_equal_amounts_with_distinct_evidence_remain_distinct(self):
        bank = rows((1, 1)); bank[1].update(reference='DIFFERENT', balance_after='80.00')
        state, _ = identities.import_bank(identities.empty_registry(), bank)
        self.assertEqual(len(record_ids(state, 'transaction')), 2)

    def test_explicit_distinct_occurrences_and_repeated_decisions_are_idempotent(self):
        bank = rows((1,)); bank.append(dict(bank[0], transaction_id='KOTAK_0002'))
        pending, source = identities.import_bank(identities.empty_registry(), bank)
        decisions = {identities.observation_id(source, 'transaction', r['transaction_id']): dict(
            target_id=None, actor='synthetic reviewer', reason='Independent bank evidence confirms two distinct movements.')
            for r in bank}
        fixed, _ = identities.import_bank(pending, bank, continuity=decisions)
        self.assertEqual(len(record_ids(fixed, 'transaction')), 2)
        repeated, _ = identities.import_bank(fixed, bank, continuity=decisions)
        self.assertEqual(repeated, fixed)
        repeated, _ = identities.import_bank(fixed, bank)
        self.assertEqual(repeated, fixed)


class PipelineIdentityTests(unittest.TestCase):
    def test_complete_snapshot_mapping_is_idempotent(self):
        data = snapshot(); before = deepcopy(data)
        state = identities.migrate(data)
        self.assertEqual(identities.migrate(data, state), state)
        self.assertEqual(data, before)
        self.assertEqual(identities.summary(state)['unresolved_mappings'], 0)
        for kind, count in [('transaction', 2), ('event', 2), ('obligation', 1), ('allocation', 1), ('settlement', 1)]:
            self.assertEqual(len(record_ids(state, kind)), count)

    def test_rule_status_and_type_changes_are_revisions_not_new_relationships(self):
        old = snapshot([dict(direction='credit', note='Alice sent me money to book flights')], [dict(payer='Alice')])
        new = snapshot([dict(direction='credit', note='I borrowed from Alice')], [dict(payer='Alice')])
        first = identities.migrate(old); second = identities.migrate(new, first)
        self.assertEqual(record_ids(first, 'transaction'), record_ids(second, 'transaction'))
        self.assertEqual(record_ids(first, 'relationship'), record_ids(second, 'relationship'))
        self.assertEqual(len(record_ids(second, 'relationship')), 1)
        target = next(iter(record_ids(first, 'relationship')))
        self.assertEqual(sum(r['record_id'] == target for r in second['revisions'].values()), 2)

    def test_counterparty_correction_preserves_origin_and_allocation_ids(self):
        old = snapshot()
        new = snapshot([origin(party='Alicia'), receipt(note='Alicia repaid in full for KOTAK_0001')])
        self.assertNotEqual(old['origins']['obligations'][0]['obligation_id'], new['origins']['obligations'][0]['obligation_id'])
        first = identities.migrate(old)
        source = identities.source_id('origins', new['origins'])
        legacy = new['origins']['obligations'][0]['obligation_id']
        pending = identities.migrate(new, first)
        self.assertIsNone(mapped(pending, source, 'obligation', legacy))
        key = identities.observation_id(source, 'obligation', legacy)
        second = identities.migrate(new, pending, continuity={key: dict(
            target_id=next(iter(record_ids(first, 'obligation'))), actor='synthetic reviewer',
            reason='The existing claimant corrected their name; not a new claim.')})
        for kind in ('transaction', 'event', 'relationship', 'obligation', 'allocation', 'settlement'):
            self.assertEqual(record_ids(first, kind), record_ids(second, kind), kind)
        self.assertGreater(len(second['revisions']), len(first['revisions']))

    def test_overlapping_snapshot_renumbering_preserves_claims_and_links(self):
        expense = dict(origin(), date='2026-08-02', narration='Original shared expense')
        payment = dict(receipt(date='2026-08-03'), narration='Original repayment')
        old = snapshot([expense, payment])
        new = snapshot([dict(date='2026-08-01', narration='Earlier transaction'), expense,
                        dict(payment, note='Alice repaid in full for KOTAK_0002')])
        first = identities.migrate(old); second = identities.migrate(new, first)
        for kind in ('event', 'relationship', 'obligation', 'allocation', 'settlement'):
            self.assertEqual(record_ids(first, kind), record_ids(second, kind), kind)
        self.assertEqual(len(record_ids(second, 'transaction')), 3)
        self.assertEqual(identities.summary(second)['unresolved_mappings'], 0)

    def test_settlement_status_change_keeps_view_identity(self):
        old = snapshot()
        new = snapshot([origin(follow_up='as of 2026-08-02, outstanding balance is 5'), receipt()])
        self.assertEqual(old['settlements']['obligation_settlement_view'][0]['status'], 'CLOSED')
        self.assertEqual(new['settlements']['obligation_settlement_view'][0]['status'], 'UNASSESSED')
        first = identities.migrate(old); second = identities.migrate(new, first)
        self.assertEqual(record_ids(first, 'settlement'), record_ids(second, 'settlement'))
        target = next(iter(record_ids(first, 'settlement')))
        self.assertEqual(sum(r['record_id'] == target for r in second['revisions'].values()), 2)

    def test_multiple_claims_are_distinct_and_correction_requires_continuity(self):
        old = snapshot([dict(note='Alice share 40, Bob share 60', components=[share('Alice', '40.00'), share('Bob', '60.00')])])
        new = snapshot([dict(note='Alicia share 40, Bob share 60', components=[share('Alicia', '40.00'), share('Bob', '60.00')])])
        state = identities.migrate(old)
        self.assertEqual(len(record_ids(state, 'obligation')), 2)
        original = next(o for o in old['origins']['obligations'] if o['counterparty'] == 'Alice')
        corrected = next(o for o in new['origins']['obligations'] if o['counterparty'] == 'Alicia')
        target = mapped(state, identities.source_id('origins', old['origins']), 'obligation', original['obligation_id'])
        pending = identities.migrate(new, state)
        source = identities.source_id('origins', new['origins'])
        self.assertIsNone(mapped(pending, source, 'obligation', corrected['obligation_id']))
        self.assertEqual(record_ids(pending, 'obligation'), record_ids(state, 'obligation'))
        key = identities.observation_id(source, 'obligation', corrected['obligation_id'])
        binding = dict(target_id=target, actor='synthetic reviewer', reason='Same claim; corrected spelling.')
        fixed = identities.migrate(new, pending, continuity={key: binding})
        self.assertEqual(mapped(fixed, source, 'obligation', corrected['obligation_id']), target)
        self.assertEqual(len(fixed['legacy_mappings'][key]['decisions']), 2)
        self.assertEqual(fixed['legacy_mappings'][key]['decisions'][-1]['continuity'], binding)
        self.assertEqual(identities.summary(fixed)['unresolved_mappings'], 0)

    def test_cross_record_links_use_existing_durable_ids(self):
        data = snapshot(); state = identities.migrate(data)
        for revision in state['revisions'].values():
            for ids in revision['references'].values():
                self.assertTrue(set(ids) <= set(state['records']))
        allocation_id = next(iter(record_ids(state, 'allocation')))
        revision = next(r for r in state['revisions'].values() if r['record_id'] == allocation_id)
        self.assertEqual(set(revision['references']['obligations']), record_ids(state, 'obligation'))
        self.assertTrue(set(revision['references']['repayments']) <= record_ids(state, 'transaction'))
        alias = identities.lookup(state, identities.source_id('classification', data['classification']), 'transaction', 'KOTAK_0001')
        self.assertIn(alias['durable_id'], record_ids(state, 'transaction'))

    def test_unmatched_event_identity_is_retained_as_unresolved(self):
        data = snapshot([origin()]); event_doc = deepcopy(data['events']['kotak'])
        event_doc['source_rows'][0]['date'] = '2026-08-09'
        event_doc['events'][0]['date'] = '2026-08-09'
        event_doc['source_rows_sha256'] = digest(event_doc['source_rows'])
        d = reconcile(data['banks']['kotak'], event_doc)
        stages = prepared([d])
        data.update(events={'kotak': event_doc}, reconciled=stages[0], relationships=stages[1],
                    classification=stages[2], coverage=stages[3], origins=stages[4], settlements=settle(*stages))
        state = identities.migrate(data)
        decision = identities.lookup(state, identities.source_id('events', event_doc), 'event', 'KOTAK_EVENT_0001')
        self.assertEqual(decision['status'], 'unresolved')
        self.assertEqual(decision['unresolved_references'], {'transaction': ['unmatched_event']})
        self.assertFalse(record_ids(state, 'event'))

    def test_registry_corruption_and_bad_continuity_fail_without_mutation(self):
        data = snapshot(); state = identities.migrate(data); before = deepcopy(state)
        broken = deepcopy(state); next(iter(broken['revisions'].values()))['record_id'] = identities.identifier('fake', 1)
        with self.assertRaisesRegex(ValueError, 'revision identity/reference'):
            identities.validate_registry(broken)
        with self.assertRaisesRegex(ValueError, 'does not identify'):
            identities.migrate(data, state, continuity={identities.identifier('fake', 1):
                dict(target_id=None, actor='test', reason='unknown observation')})
        self.assertEqual(state, before)

    def test_resolved_alias_cannot_be_reassigned(self):
        bank = rows((1, 2)); state, source = identities.import_bank(identities.empty_registry(), bank)
        key = identities.observation_id(source, 'transaction', 'KOTAK_0001')
        with self.assertRaisesRegex(ValueError, 'already resolved'):
            identities.import_bank(state, bank, continuity={key: dict(
                target_id=mapped(state, source, 'transaction', 'KOTAK_0002'), actor='test', reason='Wrong rebind')})

    def test_registry_rejects_mismatched_alias_references_and_continuity(self):
        state = identities.migrate(snapshot())
        broken = deepcopy(state)
        decision = next(m['decisions'][-1] for m in broken['legacy_mappings'].values() if m['kind'] == 'allocation')
        decision['references'] = {}
        with self.assertRaisesRegex(ValueError, 'alias/revision references disagree'):
            identities.validate_registry(broken)
        broken = deepcopy(state)
        decision = next(m['decisions'][-1] for m in broken['legacy_mappings'].values() if m['kind'] == 'allocation')
        decision['continuity'] = dict(target_id=next(iter(record_ids(state, 'transaction'))),
                                      actor='test', reason='Invalid continuity target')
        with self.assertRaisesRegex(ValueError, 'continuity decision disagrees'):
            identities.validate_registry(broken)

    def test_file_runner_writes_only_sidecar_and_is_byte_idempotent(self):
        data = snapshot()
        # The local runner consumes both supported bank documents.
        sbi = doc('SBI', [dict()])
        bank = [dict(r['bank_transaction'], account='9876543210') for r in sbi['transactions']]
        sbi = reconcile(bank, sbi['event_source_document'])
        previous = prepared([data['reconciled'][0], sbi])
        with temporary_directory() as folder:
            root = Path(folder)
            files = {}
            for d in previous[0]:
                bank = d['bank'].lower()
                files[f'output/bank/{bank}_transactions.json'] = [r['bank_transaction'] for r in d['transactions']]
                files[f'output/events/{bank}_events.json'] = d['event_source_document']
                files[f'output/reconciled/{bank}_reconciled.json'] = d
                previous[3]['sources'][bank] = dict(bank_file=f'input/{bank}.csv', bank_count=len(d['transactions']),
                    bank_date_range=['2026-08-01', '2026-08-02'], notes_file=f'input/{bank}.xlsx',
                    notes_count=len(d['event_source_document']['events']), notes_date_range=['2026-08-01', '2026-08-02'],
                    populated_meanings=0, generated_events=len(d['event_source_document']['events']),
                    unextracted_transaction_rows=0, nontransaction_meaning_cells=[], missing_workbook_ids=[])
            origins = originate(*previous[:4])
            files.update({'output/relationships/relationships.json': previous[1],
                'output/classification/effective_transactions.json': previous[2],
                'output/audit/coverage_audit.json': previous[3], 'output/obligations/obligations.json': origins,
                'output/settlements/settlements.json': settle(*previous[:4], origins)})
            for name, value in files.items():
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding='utf-8')
            for bank in ('kotak', 'sbi'):
                for suffix in ('csv', 'xlsx'):
                    path = root/f'input/{bank}.{suffix}'; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'synthetic provenance only; not parsed by identity runner')
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            result = identities.run(root)
            output = root/'output/identities/identity_registry.json'; first = output.read_bytes()
            self.assertEqual(identities.run(root), result)
            self.assertEqual(output.read_bytes(), first)
            self.assertTrue(all(p.read_bytes() == content for p, content in before.items()))
            source = root/'output/relationships/relationships.json'
            broken = deepcopy(previous[1]); broken['schema_version'] = 'invalid'
            source.write_text(json.dumps(broken), encoding='utf-8')
            with self.assertRaises(ValueError): identities.run(root)
            self.assertEqual(output.read_bytes(), first)
