"""Cross-layer invariants and regressions using synthetic financial records."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from tests.support import (
    temporary_directory, doc, with_semantics, origin, receipt, prepared, settle, single,
)
from src.settlements import run, build as settle_layers
from src.obligations import run as run_origins
from src.reconcile import reconcile
from src.integrity import audit_layers, independent_ledger
from src.relationships import flatten
from decimal import Decimal


def layers():
    documents = [doc('KOTAK', [origin(), receipt()]), doc('SBI', [dict()])]
    balanced = []
    for d in documents:
        bank = [deepcopy(r['bank_transaction']) for r in d['transactions']]
        balance = Decimal('1000.00')
        for t in bank:
            balance += Decimal(t['amount']) * (1 if t['direction'] == 'credit' else -1)
            t['balance_after'] = format(balance, '.2f')
        balanced.append(reconcile(bank, d['event_source_document']))
    data = prepared(balanced)
    banks = {d['bank'].lower(): [t['bank_transaction'] for t in d['transactions']] for d in balanced}
    events = {d['bank'].lower(): d['event_source_document'] for d in balanced}
    return [banks, events, *data[:4], data[4], settle_layers(*data)]


def failed(data):
    return {c['check'] for c in audit_layers(*data) if c['status'] == 'FAIL'}


class IntegrityRegressionTests(unittest.TestCase):
    def test_split_full_settlement_statement_is_scoped_to_its_origin(self):
        note = 'Alice repaid KOTAK_0001: 95 full settlement; KOTAK_0002: 95'
        r = settle([doc('KOTAK', [origin(), origin(), receipt('190.00', note)])])
        rows = {v['obligation_id']: v for v in r['obligation_settlement_view']}
        allocated = {a['obligation_id']: a for a in r['allocations']}
        self.assertEqual(sum(v['status'] == 'CLOSED' for v in rows.values()), 1)
        self.assertEqual(sum(a['full_settlement_intent'] for a in allocated.values()), 1)

    def test_positive_followup_balance_blocks_minor_writeoff(self):
        payment = receipt('95.00')
        payment['follow_up'] = 'as of 2026-08-02, outstanding balance is 5'
        r = settle([doc('KOTAK', [origin(), payment])])
        row = single(r)
        self.assertEqual(row['status'], 'PARTIAL')
        self.assertEqual(row['outstanding_amount'], '5.00')
        self.assertEqual(row['adjustment'], '0.00')

    def test_strength_does_not_mean_explicit_confirmation(self):
        ds = [with_semantics(doc('SBI', [dict(amount='3000.00',
            note='Dad will send it back right away, he needed it temporarily.',
            narration='UPI/DR/123456789012/Dad/ICIC/dad@bank/UPI'),
            dict(direction='credit', amount='3000.00', narration='UPI/CR/223456789012/Dad/ICIC/dad@bank/UPI')]),
            [dict(payee='Dad')])]
        r = settle(ds)
        self.assertEqual(single(r)['status'], 'CLOSED')
        self.assertEqual(r['allocations'][0]['evidence_strength'], 'high')
        self.assertEqual(r['allocations'][0]['confirmation_method'], 'inferred')
        self.assertEqual(single(r)['confirmation_methods'], ['inferred'])

    def test_downstream_audit_report_does_not_change_upstream_fingerprints(self):
        data = prepared([doc('KOTAK', [origin()]), doc('SBI', [receipt()])])
        with temporary_directory() as temp:
            root = Path(temp)
            files = {f'output/reconciled/{d["bank"].lower()}_reconciled.json': d for d in data[0]}
            files.update({'output/relationships/relationships.json': data[1],
                'output/classification/effective_transactions.json': data[2],
                'output/audit/coverage_audit.json': data[3],
                'output/obligations/obligations.json': dict(data[4], verification={'protected_file_sha256': {}, 'source_files_unchanged': True})})
            for name, value in files.items():
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(value), encoding='utf-8')
            first_origins = run_origins(root)
            first = run(root)
            (root / 'output/audit/financial_integrity.json').write_text('{"audit": "downstream"}', encoding='utf-8')
            self.assertEqual(run_origins(root), first_origins)
            self.assertEqual(run(root), first)


class CrossLayerIntegrityTests(unittest.TestCase):
    def test_consistent_layers_pass_without_mutation(self):
        data = layers(); before = deepcopy(data)
        self.assertEqual(failed(data), set())
        self.assertEqual(data, before)

    def test_duplicate_bank_row_is_detected(self):
        data = layers(); data[0]['kotak'].append(deepcopy(data[0]['kotak'][0]))
        self.assertIn('bank_schema_ids_and_duplicate_detection', failed(data))

    def test_balance_discontinuity_is_independently_detected(self):
        data = layers(); data[0]['kotak'][1]['balance_after'] = '999.99'
        self.assertIn('exact_bank_balance_continuity', failed(data))

    def test_changed_semantic_meaning_is_detected(self):
        data = deepcopy(layers()); data[1]['kotak']['events'][0]['purpose_context'] = 'Invented purpose'
        self.assertIn('reconciliation_coverage_and_full_meanings', failed(data))

    def test_missing_source_event_is_not_treated_as_missing_notes(self):
        data = layers(); data[1]['kotak']['events'].pop()
        self.assertIn('stage_2_schema_and_source_facts', failed(data))

    def test_reimbursement_or_advance_income_flag_is_detected(self):
        data = layers(); data[4]['transactions'][0]['effective']['income'] = True
        self.assertIn('classification_semantics_and_counting', failed(data))

    def test_duplicate_relationship_is_detected(self):
        data = layers(); data[3]['relationships'].append(deepcopy(data[3]['relationships'][0]))
        self.assertIn('relationship_support_and_uniqueness', failed(data))

    def test_repayment_overallocation_is_independently_detected(self):
        data = layers(); data[7]['allocations'][0]['amount'] = '100.01'
        with self.assertRaisesRegex(ValueError, 'overallocated'):
            independent_ledger(data[6], data[7], flatten(data[2]), data[4])

    def test_duplicate_allocation_is_independently_detected(self):
        data = layers(); data[7]['allocations'].append(deepcopy(data[7]['allocations'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate allocation'):
            independent_ledger(data[6], data[7], flatten(data[2]), data[4])

    def test_unassessed_zero_is_independently_rejected(self):
        data = layers(); data[7]['obligation_settlement_view'][0]['status'] = 'UNASSESSED'
        with self.assertRaisesRegex(ValueError, 'unassessed confused with verified zero'):
            independent_ledger(data[6], data[7], flatten(data[2]), data[4])

    def test_lost_classification_overlay_is_detected(self):
        data = layers(); data[7]['classification_effects'] = []
        with self.assertRaisesRegex(ValueError, 'overlay coverage'):
            independent_ledger(data[6], data[7], flatten(data[2]), data[4])

    def test_incorrect_confirmation_metadata_is_rejected(self):
        data = layers(); data[7]['allocations'][0]['confirmation_method'] = 'explicitly_confirmed_by_bank'
        with self.assertRaisesRegex(ValueError, 'confirmation method'):
            independent_ledger(data[6], data[7], flatten(data[2]), data[4])

    def test_changed_origin_principal_or_snapshot_is_detected(self):
        data = layers(); data[6]['obligations'][0]['original_amount'] = '105.00'
        self.assertIn('obligation_origin_amounts_and_provenance', failed(data))
