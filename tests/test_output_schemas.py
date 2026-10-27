"""Synthetic contracts and malformed-file regressions; no private inputs or API calls."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from src import classification, obligations, reconcile, relationships, settlements
from src.output_models import (
    BankTransactions, ClassificationOutput, CoverageOutput, EventDocument,
    ObligationsFileOutput, ObligationsOutput, ReconciliationOutput, RelationshipsOutput,
    SettlementsFileOutput, SettlementsOutput,
)
from src.schema_validation import parse_document, validate_schema
from src.validation import write_json
from tests.support import doc, origin, prepared, receipt, settle, temporary_directory


class OutputSchemaTests(unittest.TestCase):
    def setUp(self):
        self.data = prepared([doc('KOTAK', [origin(), receipt(), dict()]), doc('SBI', [dict()])])
        self.documents, self.rels, self.classified, self.coverage, self.origins = self.data
        self.settled = settlements.build(*self.data)
        self.examples = [
            (EventDocument, self.documents[0]['event_source_document']),
            (ReconciliationOutput, self.documents[0]),
            (RelationshipsOutput, self.rels),
            (ClassificationOutput, self.classified),
            (CoverageOutput, self.coverage),
            (ObligationsOutput, self.origins),
            (SettlementsOutput, self.settled),
        ]

    def reject(self, schema, value, field):
        with self.assertRaises(ValueError) as error:
            validate_schema(schema, value, 'synthetic stage')
        self.assertIn('synthetic stage schema validation failed', str(error.exception))
        self.assertIn(field, str(error.exception))

    def test_all_contracts_validate_without_mutation_or_defaults(self):
        for schema, value in self.examples:
            with self.subTest(schema=schema.__name__):
                before = json.dumps(value, ensure_ascii=False)
                self.assertIs(validate_schema(schema, value, schema.__name__), value)
                self.assertEqual(before, json.dumps(value, ensure_ascii=False))
        self.assertNotIn('verification', self.origins)
        self.assertNotIn('local_corrections', self.documents[0]['event_source_document'])

    def test_versions_are_required_exact_and_not_coerced(self):
        for schema, value in self.examples:
            for version in ('unsupported', 1, None):
                with self.subTest(schema=schema.__name__, version=version):
                    self.reject(schema, dict(value, schema_version=version), 'schema_version')
            missing = deepcopy(value); del missing['schema_version']
            self.reject(schema, missing, 'schema_version')

    def test_wrong_top_level_types_and_unknown_fields_rejected(self):
        for schema, value in self.examples:
            for malformed in (None, [], 'document'):
                with self.subTest(schema=schema.__name__, malformed=malformed):
                    self.reject(schema, malformed, '<document>')
            self.reject(schema, dict(value, unexpected=True), 'unexpected')

    def test_nested_records_are_complete_and_closed(self):
        cases = [(ReconciliationOutput, self.documents[0], 'transactions', 'match_status'),
                 (RelationshipsOutput, self.rels, 'relationships', 'details'),
                 (ClassificationOutput, self.classified, 'transactions', 'provenance'),
                 (ObligationsOutput, self.origins, 'obligations', 'evidence'),
                 (SettlementsOutput, self.settled, 'allocations', 'confirmation_method')]
        for schema, value, collection, field in cases:
            with self.subTest(schema=schema.__name__):
                broken = deepcopy(value); del broken[collection][0][field]
                self.reject(schema, broken, f'{collection}.0.{field}')
                broken = deepcopy(value); broken[collection][0]['unexpected'] = True
                self.reject(schema, broken, f'{collection}.0.unexpected')
                broken = deepcopy(value); broken[collection] = {}
                self.reject(schema, broken, collection)

    def test_invalid_ids_rejected_at_each_stage(self):
        cases = [(RelationshipsOutput, self.rels, 'relationships', 'relationship_id'),
                 (ClassificationOutput, self.classified, 'transactions', 'transaction_id'),
                 (ObligationsOutput, self.origins, 'obligations', 'obligation_id'),
                 (SettlementsOutput, self.settled, 'allocations', 'allocation_id')]
        for schema, value, collection, field in cases:
            for identifier in ('BAD_ID', 1, None):
                broken = deepcopy(value); broken[collection][0][field] = identifier
                self.reject(schema, broken, f'{collection}.0.{field}')
        broken = deepcopy(self.documents[0]); broken['transactions'][0]['event']['event_id'] = 'bad'
        self.reject(ReconciliationOutput, broken, 'transactions.0.event.event_id')
        broken = deepcopy(self.documents[0]); broken['event_decisions'][0]['source_id'] = 'sheet:0'
        self.reject(ReconciliationOutput, broken, 'event_decisions.0.source_id')

    def test_reference_ids_and_fingerprints_are_typed(self):
        broken = deepcopy(self.settled); broken['allocations'][0]['relationship_ids'] = ['bad']
        self.reject(SettlementsOutput, broken, 'allocations.0.relationship_ids.0')
        broken = deepcopy(self.origins); broken['obligations'][0]['evidence']['source_row_sha256'] = 'bad'
        self.reject(ObligationsOutput, broken, 'source_row_sha256')
        broken = deepcopy(self.classified); broken['input_fingerprints']['reconciled']['UNSUPPORTED_BANK'] = 'a' * 64
        self.reject(ClassificationOutput, broken, 'input_fingerprints.reconciled')

    def test_relationship_type_regression_rejected_by_existing_validator(self):
        broken = deepcopy(self.rels); broken['relationships'][0]['type'] = 'INVALID_TYPE'
        with self.assertRaisesRegex(ValueError, r'relationships\.0\.type'):
            relationships.validate_relationships(broken, relationships.flatten(self.documents))

    def test_classification_treatment_regression_rejected_by_existing_validator(self):
        broken = deepcopy(self.classified); broken['transactions'][0]['effective']['treatment'] = 'INVALID_TREATMENT'
        with self.assertRaisesRegex(ValueError, r'transactions\.0\.effective\.treatment'):
            classification.validate_view(broken, relationships.flatten(self.documents))

    def test_controlled_statuses_types_and_methods_are_strict(self):
        cases = [(RelationshipsOutput, self.rels, 'relationships', 'status'),
                 (ObligationsOutput, self.origins, 'obligations', 'type'),
                 (ObligationsOutput, self.origins, 'obligations', 'settlement_status'),
                 (SettlementsOutput, self.settled, 'obligation_settlement_view', 'status'),
                 (SettlementsOutput, self.settled, 'allocations', 'confirmation_method')]
        for schema, value, collection, field in cases:
            broken = deepcopy(value); broken[collection][0][field] = 'invented'
            self.reject(schema, broken, f'{collection}.0.{field}')
        broken = deepcopy(self.classified); broken['transactions'][0]['effective']['status'] = 'unresolved'
        self.reject(ClassificationOutput, broken, 'effective.status')

    def test_money_dates_and_booleans_are_not_coerced(self):
        for field, values in [('amount', (100, 100.0, '100', 'NaN', '-1.00')),
                              ('transaction_date', ('2026-02-30', '01/08/2026', 20260801))]:
            for value in values:
                broken = deepcopy(self.classified); broken['transactions'][0][field] = value
                self.reject(ClassificationOutput, broken, f'transactions.0.{field}')
        for flag in (0, 1, 'false'):
            broken = deepcopy(self.classified); broken['transactions'][0]['effective']['expense'] = flag
            self.reject(ClassificationOutput, broken, 'effective.expense')
            broken = deepcopy(self.origins); broken['policy']['settlements_evaluated'] = flag
            self.reject(ObligationsOutput, broken, 'policy.settlements_evaluated')

    def test_report_counts_and_controlled_map_keys_are_strict(self):
        for value in (-1, '1', True):
            broken = deepcopy(self.rels); broken['report']['transactions'] = value
            self.reject(RelationshipsOutput, broken, 'report.transactions')
        broken = deepcopy(self.classified); broken['report']['by_effective_treatment']['invented'] = 1
        self.reject(ClassificationOutput, broken, 'by_effective_treatment')

    def test_bank_container_and_prefix_rejected(self):
        bank = [r['bank_transaction'] for r in self.documents[0]['transactions']]
        self.reject(BankTransactions, {}, '<document>')
        broken = deepcopy(bank); broken[0]['transaction_id'] = 'SBI_0001'
        self.reject(BankTransactions, broken, 'bank prefix')

    def test_details_must_match_relationship_contract(self):
        broken = deepcopy(self.rels); broken['relationships'][0]['details'] = {}
        self.reject(RelationshipsOutput, broken, 'type/details')
        broken = deepcopy(self.rels); broken['relationships'][0]['details']['unrecognized'] = True
        self.reject(RelationshipsOutput, broken, 'unrecognized')

    def test_null_unknown_unresolved_and_unassessed_are_preserved(self):
        result = settle([doc('KOTAK', [origin(), dict()])])
        before = deepcopy(result)
        validate_schema(SettlementsOutput, result, 'settlements')
        self.assertEqual(result, before)
        self.assertEqual(result['obligation_settlement_view'][0]['status'], 'UNASSESSED')
        self.assertIsNone(result['obligation_settlement_view'][0]['outstanding_amount'])
        unknown = next(t for t in self.classified['transactions'] if t['transaction_id'] == 'KOTAK_0003')
        self.assertIsNone(unknown['semantic'])
        self.assertEqual(unknown['effective']['treatment'], 'unknown')
        self.assertIsNone(unknown['effective']['expense'])
        unclear = origin(); unclear['note'] = 'Alice share, will send'; unclear['components'][0]['amount'] = None
        _, _, _, _, claims = prepared([doc('KOTAK', [unclear])])
        self.assertIsNone(claims['obligations'][0]['original_amount'])
        validate_schema(ObligationsOutput, claims, 'unknown principal')

    def test_unassessed_cannot_be_confused_with_known_zero(self):
        broken = deepcopy(self.settled); broken['obligation_settlement_view'][0]['status'] = 'UNASSESSED'
        self.reject(SettlementsOutput, broken, 'UNASSESSED requires null')
        broken = deepcopy(self.settled); broken['obligation_settlement_view'][0]['outstanding_amount'] = None
        self.reject(SettlementsOutput, broken, 'assessed statuses require')

    def test_signed_adjustments_and_residuals_remain_valid(self):
        result = settle([doc('KOTAK', [origin(), receipt('102.00',
            'Alice repaid in full for KOTAK_0001; extra 2 rounding')])])
        validate_schema(SettlementsOutput, result, 'settlements')
        self.assertEqual(result['obligation_settlement_view'][0]['adjustment'], '-2.00')

    def test_local_correction_history_is_preserved(self):
        document = deepcopy(self.documents[0]['event_source_document'])
        document['local_corrections'] = [dict(source_id='sheet:1', reason='Approved synthetic correction',
            changes={'description': {'before': '', 'after': None},
                     'entities': {'before': [], 'after': []}})]
        document['original_response_sha256'] = 'a' * 64
        before = deepcopy(document)
        validate_schema(EventDocument, document, 'events')
        self.assertEqual(document, before)
        document['local_corrections'][0]['changes']['invented'] = {'before': None, 'after': None}
        self.reject(EventDocument, document, 'changes.invented')

    def test_file_verification_is_required_and_typed(self):
        for schema, value, hashes, flag in (
            (ObligationsFileOutput, self.origins, 'protected_file_sha256', 'source_files_unchanged'),
            (SettlementsFileOutput, self.settled, 'source_sha256', 'sources_unchanged'),
        ):
            self.reject(schema, value, 'verification')
            self.reject(schema, dict(value, verification={hashes: {}}), flag)
            validate_schema(schema, dict(value, verification={hashes: {}, flag: True}), 'file')
            self.reject(schema, dict(value, verification={hashes: {'file.json': 'invalid'}, flag: True}), 'file.json')

    def test_errors_include_stage_and_path_without_private_values(self):
        broken = deepcopy(self.classified)
        broken['transactions'][0]['amount'] = 'PRIVATE-NOTE-OR-ACCOUNT'
        with self.assertRaises(ValueError) as error:
            validate_schema(ClassificationOutput, broken, 'classification input.json')
        message = str(error.exception)
        self.assertIn('classification input.json', message)
        self.assertIn('transactions.0.amount', message)
        self.assertNotIn('PRIVATE-NOTE-OR-ACCOUNT', message)

    def test_malformed_json_has_actionable_location(self):
        with self.assertRaisesRegex(ValueError, r'input.json: invalid JSON \(line 1, column'):
            parse_document('{', ReconciliationOutput, 'input.json')

    def test_invalid_bank_records_cannot_overwrite_output(self):
        bank = [deepcopy(r['bank_transaction']) for r in self.documents[0]['transactions']]
        bank[0]['amount'] = 100.0
        with temporary_directory() as temporary:
            output = Path(temporary)/'bank.json'
            output.write_bytes(b'existing bank output')
            with self.assertRaisesRegex(ValueError, 'amount'):
                write_json(bank, output)
            self.assertEqual(output.read_bytes(), b'existing bank output')

    def test_missing_inputs_fail_before_calculation(self):
        broken = deepcopy(self.classified); del broken['schema_version']
        with patch('src.obligations.flatten') as calculate:
            with self.assertRaisesRegex(ValueError, 'classification input.*schema_version'):
                obligations.build(self.documents, self.rels, broken, self.coverage)
            calculate.assert_not_called()
        broken = deepcopy(self.origins); broken['schema_version'] = 'legacy-unknown'
        with patch('src.settlements.build_origins') as calculate:
            with self.assertRaisesRegex(ValueError, 'obligations input.*schema_version'):
                settlements.build(*self.data[:4], broken)
            calculate.assert_not_called()

    def test_loaded_stage_boundaries_reject_before_overwriting_outputs(self):
        cases = [
            ('output/events/kotak_events.json', 'output/reconciled/kotak_reconciled.json',
             lambda r: reconcile.run(r/'output/bank/kotak_transactions.json',
                 r/'output/events/kotak_events.json', r/'output/reconciled/kotak_reconciled.json')),
            ('output/reconciled/kotak_reconciled.json', 'output/relationships/relationships.json',
             lambda r: relationships.run([r/'output/reconciled/kotak_reconciled.json'], r/'output/relationships/relationships.json')),
            ('output/relationships/relationships.json', 'output/classification/effective_transactions.json',
             lambda r: classification.run([r/f'output/reconciled/{b}_reconciled.json' for b in ('kotak','sbi')],
                 r/'output/relationships/relationships.json', r/'output/classification/effective_transactions.json')),
            ('output/audit/coverage_audit.json', 'output/obligations/obligations.json', obligations.run),
            ('output/obligations/obligations.json', 'output/settlements/settlements.json', settlements.run),
        ]
        for source, destination, runner in cases:
            with self.subTest(stage=destination), temporary_directory() as temporary:
                root = Path(temporary)
                files = {f'output/reconciled/{d["bank"].lower()}_reconciled.json': d for d in self.documents}
                files.update({f'output/events/{d["bank"].lower()}_events.json': d['event_source_document'] for d in self.documents})
                files.update({f'output/bank/{d["bank"].lower()}_transactions.json': [r['bank_transaction'] for r in d['transactions']] for d in self.documents})
                files.update({'output/relationships/relationships.json': self.rels,
                    'output/classification/effective_transactions.json': self.classified,
                    'output/audit/coverage_audit.json': self.coverage,
                    'output/obligations/obligations.json': dict(self.origins,
                        verification={'protected_file_sha256': {}, 'source_files_unchanged': True})})
                for name, value in files.items():
                    path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(value), encoding='utf-8')
                output = root/destination; output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b'existing output sentinel')
                bad_version = dict(files[source], schema_version='unsupported')
                missing_version = {k: v for k, v in files[source].items() if k != 'schema_version'}
                for payload, expected in [(json.dumps(bad_version), 'schema_version'),
                                          (json.dumps(missing_version), 'schema_version'),
                                          ('{', 'invalid JSON'), ('[]', 'schema validation failed')]:
                    with self.subTest(payload=expected):
                        (root/source).write_text(payload, encoding='utf-8')
                        before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                        with self.assertRaisesRegex(ValueError, expected):
                            runner(root)
                        self.assertEqual(output.read_bytes(), b'existing output sentinel')
                        after = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                        self.assertEqual(before, after)
