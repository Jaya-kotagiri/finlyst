"""Versioned JSON contracts. These models validate; they never rewrite financial data."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, Field, TypeAdapter, model_validator

from .event_models import Amount, Component, Entity, FinancialEvent, StrictModel


def nonblank(value):
    if not value.strip():
        raise ValueError('must contain non-whitespace text')
    return value


def calendar_day(value):
    date.fromisoformat(value)
    return value


def positive(value):
    if Decimal(value) <= 0:
        raise ValueError('must be greater than zero')
    return value


def true_flag(value):
    if value is not True:
        raise ValueError('must be true')
    return value


def false_flag(value):
    if value is not False:
        raise ValueError('must be false')
    return value


Text = Annotated[str, AfterValidator(nonblank)]
Day = Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$'), AfterValidator(calendar_day)]
SignedAmount = Annotated[str, Field(pattern=r'^-?\d+\.\d{2}$')]
PositiveAmount = Annotated[Amount, AfterValidator(positive)]
Count = Annotated[int, Field(ge=0)]
PositiveCount = Annotated[int, Field(gt=0)]
TrueFlag = Annotated[bool, AfterValidator(true_flag)]
FalseFlag = Annotated[bool, AfterValidator(false_flag)]
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Bank = Literal['KOTAK', 'SBI']
BankIdentifier = Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9_]*$')]
TransactionId = Annotated[str, Field(pattern=r'^(?:KOTAK|SBI)_\d{4,}$')]
EventId = Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9_]*_EVENT_\d{4,}$')]
SourceId = Annotated[str, Field(pattern=r'^.+:[1-9]\d*$')]
RelationshipId = Annotated[str, Field(pattern=r'^REL_[0-9a-f]{16}$')]
ObligationId = Annotated[str, Field(pattern=r'^OBL_[0-9a-f]{20}$')]
ClarificationId = Annotated[str, Field(pattern=r'^CLR_[0-9a-f]{20}$')]
AllocationId = Annotated[str, Field(pattern=r'^ALLOC_[0-9a-f]{20}$')]
CandidateId = Annotated[str, Field(pattern=r'^CAND_[0-9a-f]{20}$')]
SettlementQuestionId = Annotated[str, Field(pattern=r'^SETCLR_[0-9a-f]{20}$')]
Direction = Literal['debit', 'credit']
Confidence = Literal['high', 'medium', 'low']
ClaimDirection = Literal['receivable', 'payable']
RelationshipType = Literal['self_transfer', 'cash_conversion', 'loan_advance', 'loan_borrowing',
    'reimbursement', 'reimbursement_expected', 'planned_transfer', 'payment_on_behalf',
    'purpose_specific_funding', 'possible_loan_return']
RelationshipStatus = Literal['confirmed', 'identified', 'source_asserted', 'planned', 'unresolved']
SpecialTreatment = Literal['self_transfer', 'cash_conversion', 'loan_advance', 'loan_borrowing',
    'reimbursement_received', 'recoverable_or_shared_expense', 'salary_income']
RelationshipTreatment = SpecialTreatment | Literal['unclassified']
EffectiveTreatment = SpecialTreatment | Literal['unknown', 'expense']
ClassificationSource = Literal['insufficient_evidence', 'financial_relationships', 'bank_facts', 'matched_semantics']
ClassificationRule = Literal['no_supported_financial_treatment', 'supported_3B_treatment_precedes_semantic_type',
    'funding_or_beneficiary_terms_unresolved', 'explicit_bank_fee_narration',
    'special_semantic_claim_without_financial_support', 'noted_ordinary_purchase_or_service']
ReviewReason = Literal['financial_treatment_unknown', 'personal_expense_allocation_deferred',
    'semantic_uncertainty', 'relationship_unresolved']
ObligationType = Literal['loan_advance', 'loan_borrowing', 'reimbursement', 'explicit_payable']
SettlementStatus = Literal['OPEN', 'PARTIAL', 'CLOSED', 'UNASSESSED']
ConfirmationMethod = Literal['inferred', 'source_supported', 'explicit_source_allocation']


class BankTransaction(StrictModel):
    transaction_id: TransactionId
    bank: Bank
    account: Text | None
    transaction_date: Day
    amount: Amount
    direction: Direction
    raw_description: Text
    reference: Text | None
    balance_after: SignedAmount | None

    @model_validator(mode='after')
    def bank_prefix(self):
        if not self.transaction_id.startswith(self.bank + '_'):
            raise ValueError('transaction_id must use the bank prefix')
        return self


BankTransactions = TypeAdapter(list[BankTransaction])


class SourceRow(StrictModel):
    source_id: SourceId
    date: Day
    amount: Amount
    direction: Direction
    narration: str | None = None
    note: str | None = None
    follow_up: str | None = None
    reference: str | None = None
    value_date: Day | None = None


class SavedEvent(FinancialEvent):
    event_id: EventId
    source_id: SourceId


class EventReport(StrictModel):
    events: Count
    batches: Count
    validated: TrueFlag
    duplicate_events: Count
    ambiguous_events: Count
    low_confidence_events: Count
    rows_without_notes: Count
    input_tokens: Count
    output_tokens: Count


T = TypeVar('T')


class Change(StrictModel, Generic[T]):
    before: T
    after: T


class EventChanges(StrictModel):
    # Historical 'before' text may contain the empty descriptions that were corrected.
    event_type: Change[str | None] | None = None
    merchant: Change[str | None] | None = None
    payee: Change[str | None] | None = None
    payer: Change[str | None] | None = None
    category: Change[str | None] | None = None
    subcategory: Change[str | None] | None = None
    description: Change[str | None] | None = None
    purpose_context: Change[str | None] | None = None
    entities: Change[list[Entity]] | None = None
    components: Change[list[Component]] | None = None
    confidence: Change[Confidence] | None = None
    ambiguities: Change[list[str]] | None = None
    matching_clues: Change[list[str]] | None = None
    additional_context: Change[list[str]] | None = None


class LocalCorrection(StrictModel):
    source_id: SourceId
    reason: Text
    changes: EventChanges


class EventDocument(StrictModel):
    schema_version: Literal['1.0']
    bank: BankIdentifier
    source_file: Text
    source_rows_sha256: Sha256
    model: Text
    report: EventReport
    source_rows: list[SourceRow]
    events: list[SavedEvent]
    local_corrections: list[LocalCorrection] = Field(default_factory=list)
    original_response_sha256: Sha256 | None = None


class ReferencePrecision(StrictModel):
    kind: Literal['decimal_precision_loss_or_formatting']
    source_reference: Text
    authoritative_reference: Text
    rounding_quantum: Annotated[str, Field(pattern=r'^\d+$')]
    significant_digits: Annotated[int, Field(ge=3)]


class CandidateEvidence(StrictModel):
    date: TrueFlag
    amount: TrueFlag
    direction: TrueFlag
    reference: bool
    reference_conflict: bool
    reference_precision: ReferencePrecision | None
    precision_resolution_supported: bool
    supported_merchant_payee_names: list[str]
    narration_ignoring_whitespace: bool


class MatchEvidence(CandidateEvidence):
    chronological_sequence: TrueFlag
    source_id: SourceId


MatchStatus = Literal['matched', 'ambiguous', 'unmatched']


class MatchCandidate(StrictModel):
    transaction_id: TransactionId
    evidence: CandidateEvidence


class EventDecision(StrictModel):
    event_id: EventId
    source_id: SourceId
    status: MatchStatus
    transaction_id: TransactionId | None
    reason: Literal['unique_strong_match_reference_precision_resolved', 'unique_strong_sequence_consistent',
        'reference_conflict', 'insufficient_identity_or_sequence_conflict', 'no_compatible_bank_facts']
    candidates: list[MatchCandidate]


class ReconciledTransaction(StrictModel):
    bank_transaction: BankTransaction
    match_status: MatchStatus
    event: SavedEvent | None
    category: str | None
    meaning_needs_review: bool
    matching_evidence: MatchEvidence | None
    candidate_event_ids: list[EventId]


class ReconciliationFingerprints(StrictModel):
    bank: Sha256
    events: Sha256


class ReconciliationReport(StrictModel):
    bank_transactions: Count
    events: Count
    matched: Count
    unmatched_bank: Count
    ambiguous_bank: Count
    unmatched_events: Count
    ambiguous_events: Count
    meaning_review: Count


class ReconciliationOutput(StrictModel):
    schema_version: Literal['3A.2']
    bank: Bank
    input_fingerprints: ReconciliationFingerprints
    event_source_document: EventDocument
    transactions: list[ReconciledTransaction]
    event_decisions: list[EventDecision]
    report: ReconciliationReport


ReconciledDocuments = TypeAdapter(list[ReconciliationOutput])


RelationshipRule = Literal['same_upi_id_date_amount_opposite_owned_accounts', 'duplicate_transfer_candidates',
    'explicit_future_transfer_instruction', 'cash_amount_not_supported', 'explicit_atm_withdrawal',
    'actual_bank_debit_and_explicit_cash_received_note', 'explicit_source_loan_intent_and_bank_direction',
    'actual_credit_matches_note_supported_expense_share', 'expense_link_not_unique_or_amount_not_supported',
    'source_supported_shared_or_recoverable_expense', 'beneficiary_known_repayment_terms_unknown',
    'explicit_funding_purpose_terms_unspecified', 'bank_counterparty_amount_timing_candidate_only',
    'competing_credits_for_same_share']


class RelationshipEvidence(StrictModel):
    transaction_id: TransactionId
    event_id: EventId | None
    source_id: SourceId | None
    note: str | None
    follow_up: str | None


class EmptyDetails(StrictModel):
    pass


class TransferDetails(StrictModel):
    shared_upi_id: Annotated[str, Field(pattern=r'^\d{12}$')]
    execution: Literal['bank_confirmed']


class PlannedDetails(StrictModel):
    execution: Literal['not_established']
    creates_cashflow: FalseFlag


class CashMovement(StrictModel):
    direction: Literal['in']
    amount: Amount


class CashDetails(StrictModel):
    cash_movement: CashMovement
    cash_evidence: Literal['bank_withdrawal', 'user_reported']
    not_current_cash_balance: TrueFlag


class LoanDetails(StrictModel):
    repayment_assumed: FalseFlag


class ReimbursementDetails(StrictModel):
    credit_amount: Amount
    does_not_close_obligation: TrueFlag
    claim_party: Text


class UnresolvedReimbursementDetails(StrictModel):
    receipt_classification_supported: TrueFlag
    allocation_unknown: TrueFlag


class ExpectedReimbursementDetails(StrictModel):
    claims_are_unverified_event_components: TrueFlag
    source_ambiguities: list[str]
    no_outstanding_balance_computed: TrueFlag


class FundingDetails(StrictModel):
    gift_or_loan_not_assumed: TrueFlag
    expense_allocation_not_inferred: TrueFlag


class PossibleReturnDetails(StrictModel):
    repayment_not_confirmed: TrueFlag


class RelationshipCountingPolicy(StrictModel):
    no_independent_cashflow: TrueFlag
    instruction: Text


class Relationship(StrictModel):
    relationship_id: RelationshipId
    type: RelationshipType
    transaction_ids: Annotated[list[TransactionId], Field(min_length=1)]
    status: RelationshipStatus
    rule: RelationshipRule
    confidence: Confidence
    purpose: str | None
    candidate_transaction_ids: list[TransactionId]
    evidence: list[RelationshipEvidence]
    details: (EmptyDetails | TransferDetails | PlannedDetails | CashDetails | LoanDetails |
              ReimbursementDetails | UnresolvedReimbursementDetails | ExpectedReimbursementDetails |
              FundingDetails | PossibleReturnDetails)
    financial_treatment: RelationshipCountingPolicy

    @model_validator(mode='after')
    def detail_contract(self):
        # A rule selects the existing record shape; this performs no financial matching.
        contracts = {
            'same_upi_id_date_amount_opposite_owned_accounts': (('self_transfer',), TransferDetails),
            'duplicate_transfer_candidates': (('self_transfer',), EmptyDetails),
            'explicit_future_transfer_instruction': (('planned_transfer',), PlannedDetails),
            'cash_amount_not_supported': (('cash_conversion',), EmptyDetails),
            'explicit_atm_withdrawal': (('cash_conversion',), CashDetails),
            'actual_bank_debit_and_explicit_cash_received_note': (('cash_conversion',), CashDetails),
            'explicit_source_loan_intent_and_bank_direction': (('loan_advance', 'loan_borrowing'), LoanDetails),
            'actual_credit_matches_note_supported_expense_share': (('reimbursement',), ReimbursementDetails),
            'competing_credits_for_same_share': (('reimbursement',), ReimbursementDetails),
            'expense_link_not_unique_or_amount_not_supported': (('reimbursement',), UnresolvedReimbursementDetails),
            'source_supported_shared_or_recoverable_expense': (('reimbursement_expected',), ExpectedReimbursementDetails),
            'beneficiary_known_repayment_terms_unknown': (('payment_on_behalf',), EmptyDetails),
            'explicit_funding_purpose_terms_unspecified': (('purpose_specific_funding',), FundingDetails),
            'bank_counterparty_amount_timing_candidate_only': (('possible_loan_return',), PossibleReturnDetails),
        }
        kinds, detail_type = contracts[self.rule]
        if self.type not in kinds or not isinstance(self.details, detail_type):
            raise ValueError('type/details do not match the relationship rule contract')
        return self


class TransactionTreatment(StrictModel):
    transaction_id: TransactionId
    classification: RelationshipTreatment
    income: bool | None
    expense: bool | None
    relationship_ids: list[RelationshipId]
    reason: Text


class RelationshipReport(StrictModel):
    transactions: Count
    relationships: Count
    by_status: dict[RelationshipStatus, Count]
    by_type: dict[RelationshipType, Count]
    classifications: dict[RelationshipTreatment, Count]


class RelationshipsOutput(StrictModel):
    schema_version: Literal['3B.1']
    source_fingerprints: dict[Bank, Sha256]
    relationships: list[Relationship]
    transaction_treatments: list[TransactionTreatment]
    report: RelationshipReport
    limitations: list[str]


class SemanticProjection(StrictModel):
    event_id: EventId
    source_id: SourceId
    event_type: str | None
    merchant: str | None
    payee: str | None
    payer: str | None
    purpose_context: str | None
    category: str | None
    subcategory: str | None
    description: str | None
    confidence: Confidence
    ambiguities: list[str]


class EffectiveClassification(StrictModel):
    treatment: EffectiveTreatment
    income: bool | None
    expense: bool | None
    status: Literal['unknown', 'supported', 'allocation_deferred']
    source: ClassificationSource
    rule: ClassificationRule
    confidence: Confidence
    supporting_relationship_ids: list[RelationshipId]


class RelationshipReference(StrictModel):
    relationship_id: RelationshipId
    type: RelationshipType
    status: RelationshipStatus
    role: Literal['participant', 'candidate']


class ClassificationProvenance(StrictModel):
    bank_transaction_id: TransactionId
    semantic_event_id: EventId | None
    semantic_event_sha256: Sha256 | None
    source_row_id: SourceId | None
    source_row_sha256: Sha256 | None
    original_3B_classification: RelationshipTreatment


class EffectiveTransaction(BankTransaction):
    semantic: SemanticProjection | None
    effective: EffectiveClassification
    relationships: list[RelationshipReference]
    review_reasons: list[ReviewReason]
    provenance: ClassificationProvenance


class ClassificationFingerprints(StrictModel):
    reconciled: dict[Bank, Sha256]
    relationships: Sha256


class ClassificationReport(StrictModel):
    transactions: Count
    semantic_events: Count
    without_semantic_event: Count
    by_effective_treatment: dict[EffectiveTreatment, Count]
    by_classification_source: dict[ClassificationSource, Count]
    unknown_treatment: Count
    allocation_deferred: Count
    with_unresolved_relationships: Count
    with_semantic_uncertainty: Count


class ClassificationOutput(StrictModel):
    schema_version: Literal['3C.1.1']
    input_fingerprints: ClassificationFingerprints
    transactions: list[EffectiveTransaction]
    report: ClassificationReport
    counting_policy: Text


class OriginEvidence(StrictModel):
    source_id: SourceId | None
    note: str | None
    follow_up: str | None
    bank_transaction_sha256: Sha256
    semantic_event_sha256: Sha256
    source_row_sha256: Sha256


class PrincipalEvidence(OriginEvidence):
    amount_rule: Literal['explicit_loan_intent_and_bank_principal', 'partial_or_conflicting_loan_amount',
        'explicit_counterparty_principal', 'whole_beneficiary_purchase_repayment_explicit',
        'original_principal_requires_clarification', 'share_exceeds_origin_amount', 'combined_shares_exceed_origin_amount']
    proposed_event_amounts: list[Amount]
    source_supported_amount_mentions: list[Amount]


class OriginRelationshipContext(StrictModel):
    relationship_id: RelationshipId
    origin_role: Literal['participant', 'candidate']
    transaction_ids: list[TransactionId]
    candidate_transaction_ids: list[TransactionId]
    settlement_effect: Literal['NOT_ASSESSED']


class OriginProvenance(StrictModel):
    stage: Literal['3C.2']
    effective_treatment: EffectiveTreatment


class Obligation(StrictModel):
    obligation_id: ObligationId
    type: ObligationType
    direction: ClaimDirection
    counterparty: Text | None
    purpose: str | None
    currency: Literal['INR']
    currency_basis: Literal['owned_INR_bank_statement_scope']
    original_amount: PositiveAmount | None
    origin_date: Day
    origin_status: Literal['CONFIRMED']
    settlement_status: Literal['UNASSESSED']
    requires_clarification: bool
    originating_transaction_ids: Annotated[list[TransactionId], Field(min_length=1, max_length=1)]
    semantic_event_ids: Annotated[list[EventId], Field(min_length=1, max_length=1)]
    supporting_relationship_ids: list[RelationshipId]
    existing_relationship_context: list[OriginRelationshipContext]
    confidence: Confidence
    evidence: PrincipalEvidence
    provenance: OriginProvenance


class OriginQuestion(StrictModel):
    clarification_id: ClarificationId
    transaction_ids: Annotated[list[TransactionId], Field(min_length=1, max_length=1)]
    event_ids: Annotated[list[EventId], Field(min_length=1, max_length=1)]
    obligation_ids: list[ObligationId]
    reason: Literal['obligation_existence_and_terms_unknown', 'conflicting_obligation_evidence',
        'loan_principal_or_party_unknown', 'original_share_amount_unclear', 'recoverable_party_or_terms_unsupported']
    question: Text
    status: Literal['AWAITING_USER']
    priority: Literal['normal']
    answer: None
    requested_fields: list[Literal['obligation_exists', 'direction', 'counterparty', 'original_amount', 'repayment_terms']]
    evidence: OriginEvidence
    relationship_ids: list[RelationshipId]


class DirectionAmounts(StrictModel):
    receivable: Amount
    payable: Amount


class ObligationReport(StrictModel):
    confirmed_origins: Count
    by_type: dict[ObligationType, Count]
    by_direction: dict[ClaimDirection, Count]
    known_original_principal_INR: DirectionAmounts
    unknown_original_amount: Count
    origins_requiring_clarification: Count
    clarification_candidates: Count
    settlement_status: Literal['UNASSESSED']
    totals_are_outstanding_balances: FalseFlag


class ObligationPolicy(StrictModel):
    settlements_evaluated: FalseFlag
    amounts_are_original_principal_only: TrueFlag
    known_totals_exclude_unknown_principals: TrueFlag
    clarification_updates: Text


class ObligationFingerprints(ClassificationFingerprints):
    classification: Sha256
    audit: Sha256


class ObligationVerification(StrictModel):
    protected_file_sha256: dict[Text, Sha256]
    source_files_unchanged: TrueFlag


class ObligationsOutput(StrictModel):
    schema_version: Literal['3C.2.1']
    input_fingerprints: ObligationFingerprints
    obligations: list[Obligation]
    clarification_candidates: list[OriginQuestion]
    report: ObligationReport
    policy: ObligationPolicy
    limitations: list[str]
    # Pure builders do not have file-system verification; file runners add it.
    verification: ObligationVerification | None = None


class RepaymentEvidence(StrictModel):
    transaction_id: TransactionId
    bank_transaction_sha256: Sha256
    source_id: SourceId | None
    source_row_sha256: Sha256
    semantic_event_id: EventId | None
    note: str | None
    follow_up: str | None


class Allocation(StrictModel):
    allocation_id: AllocationId
    obligation_id: ObligationId
    repayment_transaction_id: TransactionId
    amount: PositiveAmount
    currency: Literal['INR']
    rule: Literal['confirmed_3B_reimbursement', 'explicit_source_allocation',
        'explicit_repayment_origin_and_party', 'unique_immediate_bank_counterparty_return']
    relationship_ids: list[RelationshipId]
    explicit_split: bool
    full_settlement_intent: bool
    explicit_minor_overpayment: bool
    confidence: Literal['high']
    evidence_strength: Literal['high']
    confirmation_method: ConfirmationMethod
    evidence: RepaymentEvidence


class BalanceStatement(StrictModel):
    kind: Literal['zero', 'balance']
    amount: Amount | None
    as_of: Day
    transaction_id: TransactionId
    source_field: Literal['note', 'follow_up']
    source_excerpt: Text

    @model_validator(mode='after')
    def statement_shape(self):
        if (self.kind == 'zero') != (self.amount is None):
            raise ValueError('zero-repayment statements require null amount; balance statements require an amount')
        return self


class ConflictingBalanceStatements(StrictModel):
    kind: Literal['conflict']
    statements: Annotated[list[BalanceStatement], Field(min_length=1)]


class AdjustmentEvidence(StrictModel):
    allocation_ids: Annotated[list[AllocationId], Field(min_length=1)]
    rule: Literal['supported_final_residual_up_to_5', 'explicit_minor_overpayment_adjustment']


SettlementReason = Literal['historical_coverage_or_repayment_evidence_incomplete', 'original_principal_unknown',
    'unresolved_repayment_or_allocation_evidence', 'dated_balance_statement_conflicts_with_ledger',
    'verified_principal_repaid_exactly', 'minor_final_residual_adjustment', 'documented_minor_overpayment',
    'unexplained_overpayment', 'dated_explicit_no_repayments_statement',
    'dated_explicit_balance_corresponds_to_verified_ledger']
CandidateReason = Literal['competing_confirmed_links', 'invalid_or_ambiguous_explicit_allocation',
    'repayment_origin_not_unique', 'existing_relationship_requires_allocation_or_confirmation',
    'repayment_purpose_or_origin_insufficient', 'repayment_capacity_or_competing_allocations']


class SettlementView(StrictModel):
    obligation_id: ObligationId
    origin_sha256: Sha256
    direction: ClaimDirection
    currency: Literal['INR']
    original_principal: PositiveAmount | None
    verified_repayments: Amount
    verified_zero_repayments: bool
    adjustment: SignedAmount
    adjustment_evidence: AdjustmentEvidence | None
    unadjusted_residual: SignedAmount | None
    outstanding_amount: Amount | None
    status: SettlementStatus
    as_of: Day
    reason: SettlementReason
    repayment_transaction_ids: list[TransactionId]
    allocation_ids: list[AllocationId]
    balance_assertion: BalanceStatement | ConflictingBalanceStatements | None
    repayment_evidence_strength: Literal['high'] | None
    confirmation_methods: list[ConfirmationMethod]
    existing_clarification_ids: list[ClarificationId]

    @model_validator(mode='after')
    def lifecycle_shape(self):
        if (self.status == 'UNASSESSED') != (self.outstanding_amount is None):
            raise ValueError('UNASSESSED requires null outstanding; assessed statuses require a known amount')
        if self.original_principal is None and self.status != 'UNASSESSED':
            raise ValueError('unknown principal requires UNASSESSED status')
        if self.status == 'CLOSED' and Decimal(self.outstanding_amount) != 0:
            raise ValueError('CLOSED requires zero outstanding')
        return self


class SettlementCandidate(StrictModel):
    candidate_id: CandidateId
    repayment_transaction_id: TransactionId
    obligation_ids: Annotated[list[ObligationId], Field(min_length=1)]
    reason: CandidateReason
    relationship_ids: list[RelationshipId]
    bank_amount: Amount
    status: Literal['UNRESOLVED']
    evidence: RepaymentEvidence


class SettlementQuestion(StrictModel):
    clarification_id: SettlementQuestionId
    obligation_ids: Annotated[list[ObligationId], Field(min_length=1)]
    repayment_transaction_id: TransactionId | None
    candidate_id: CandidateId | None
    reason: CandidateReason | SettlementReason
    status: Literal['AWAITING_USER']
    answer: None
    question: Text


class ClassificationEffect(StrictModel):
    transaction_id: TransactionId
    original_effective_treatment: EffectiveTreatment
    verified_principal_repayment_amount: PositiveAmount
    remaining_transaction_amount_unclassified_by_this_stage: Amount
    repayment_is_income_or_expense: FalseFlag | None
    excess_treatment_requires_review: bool
    allocation_ids: Annotated[list[AllocationId], Field(min_length=1)]
    instruction: Text


class StatusCounts(StrictModel):
    OPEN: Count
    PARTIAL: Count
    CLOSED: Count
    UNASSESSED: Count


class OutstandingTotals(StrictModel):
    confirmed_outstanding: Amount
    unassessed_known_principal: Amount
    unassessed_arithmetic_residual_not_confirmed_debt: Amount
    unknown_principal_count: Count


class OutstandingByDirection(StrictModel):
    receivable: OutstandingTotals
    payable: OutstandingTotals


class SettlementReport(StrictModel):
    obligations: Count
    by_status: StatusCounts
    verified_repayments: Amount
    verified_repayments_by_direction: DirectionAmounts
    outstanding_INR: OutstandingByDirection
    signed_adjustments: SignedAmount
    unresolved_candidate_groups: Count
    new_clarifications: Count


class SettlementFingerprints(ObligationFingerprints):
    origins: Sha256


class SettlementPolicy(StrictModel):
    tolerance_INR: Literal['5.00']
    tolerance_applies_to: Literal['final_obligation_residual_with_full_settlement_evidence']
    absence_of_match_is_not_verified_zero: TrueFlag
    unassessed_residuals_are_not_confirmed_outstanding: TrueFlag


class SettlementVerification(StrictModel):
    source_sha256: dict[Text, Sha256]
    sources_unchanged: TrueFlag


class SettlementsOutput(StrictModel):
    schema_version: Literal['3C.3.2']
    input_fingerprints: SettlementFingerprints
    allocations: list[Allocation]
    obligation_settlement_view: list[SettlementView]
    unresolved_candidates: list[SettlementCandidate]
    clarification_candidates: list[SettlementQuestion]
    carried_origin_clarification_ids: list[ClarificationId]
    classification_effects: list[ClassificationEffect]
    report: SettlementReport
    policy: SettlementPolicy
    limitations: list[str]
    verification: SettlementVerification | None = None


class ObligationsFileOutput(ObligationsOutput):
    verification: ObligationVerification


class SettlementsFileOutput(SettlementsOutput):
    verification: SettlementVerification


# The coverage audit is an upstream financial-data contract consumed by obligations.
CoverageReason = Literal['supported', 'missing_workbook_row', 'ambiguous_workbook_match',
    'missing_generated_event', 'unmatched_generated_event', 'blank_note',
    'uncertain_financial_terms', 'semantic_classification_review']


class WorkbookRow(StrictModel):
    sheet: Text
    row: PositiveCount
    meaning_cell: Annotated[str, Field(pattern=r'^[A-Z]+[1-9]\d*$')]
    meaning: str | None
    follow_up: str | None
    hidden: bool
    date: Day | None = None


class CsvLocation(StrictModel):
    csv_record: PositiveCount


class WorkbookLocation(StrictModel):
    sheet: Text
    row: PositiveCount


class CoverageRow(BankTransaction):
    bank_source_file: Text
    bank_source_location: CsvLocation | WorkbookLocation
    notes_file: Text
    workbook_candidates: list[SourceId]
    workbook_row: WorkbookRow | None
    meaning_populated: bool
    follow_up_populated: bool
    extracted_in_stage_2: bool
    event_id: EventId | None
    matched_in_3A: bool
    semantic_meaning_preserved: bool
    effective_treatment: EffectiveTreatment
    classification_rule: ClassificationRule
    original_unknown: bool
    semantic: SemanticProjection | None
    relationships: list[RelationshipReference]
    review_reasons: list[ReviewReason]
    reason_category: CoverageReason
    responsible_stage: Literal['source_notes_coverage', 'source_alignment', 'stage_2', 'stage_3A',
        'source_notes_content', 'source_meaning_and_3B', 'stage_3C1'] | None
    explanation: Text


class CoverageSummary(StrictModel):
    transactions: Count
    unknown: Count
    unmatched: Count
    unknown_and_unmatched: Count
    unknown_but_matched: Count
    unmatched_but_classified: Count
    original_unknown_ids: list[TransactionId]
    unknown_with_populated_meaning: Count
    unknown_by_reason: dict[CoverageReason, Count]
    by_reason: dict[CoverageReason, Count]


class CoverageSource(StrictModel):
    bank_file: Text
    bank_count: Count
    bank_date_range: Annotated[list[Day], Field(min_length=2, max_length=2)]
    notes_file: Text
    notes_count: Count
    notes_date_range: Annotated[list[Day], Field(min_length=2, max_length=2)]
    populated_meanings: Count
    generated_events: Count
    unextracted_transaction_rows: Count
    nontransaction_meaning_cells: list[WorkbookRow]
    missing_workbook_ids: list[TransactionId]


class CoverageVerification(StrictModel):
    source_sha256: dict[Text, Sha256]
    sources_unchanged: TrueFlag
    classification_rebuild_identical: TrueFlag
    deterministic_fixes: Count
    api_calls: Count


class CoverageOutput(StrictModel):
    schema_version: Literal['3C.1A.1']
    summary: CoverageSummary
    sources: dict[Literal['kotak', 'sbi'], CoverageSource]
    verification: CoverageVerification
    transactions: list[CoverageRow]
