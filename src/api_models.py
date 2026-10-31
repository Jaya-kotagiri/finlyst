"""HTTP contracts reuse financial schemas; monetary values remain exact strings."""

from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .event_models import Amount, StrictModel
from .identity_models import Decision, DurableId, Kind
from .output_models import (Allocation, Bank, BankTransaction, ClassificationEffect, Count, Day,
    EffectiveTransaction, EffectiveTreatment, Obligation, Relationship, SavedEvent, SettlementStatus,
    SettlementView, SignedAmount, Text, TransactionId)

T = TypeVar('T')


class Envelope(StrictModel, Generic[T]):
    api_version: Literal['4A.1'] = '4A.1'
    snapshot_id: DurableId | None = None
    data: T


class Pagination(StrictModel):
    total: Count
    offset: Count
    limit: Annotated[int, Field(ge=1, le=100)]
    returned: Count
    next_offset: Count | None


class TransactionPage(Envelope[list['TransactionRecord']]):
    pagination: Pagination


class FieldError(StrictModel):
    location: list[str]
    message: str


class ErrorDetail(StrictModel):
    code: str
    message: str
    fields: list[FieldError] = Field(default_factory=list)


class ErrorResponse(StrictModel):
    api_version: Literal['4A.1'] = '4A.1'
    error: ErrorDetail


class SnapshotInfo(StrictModel):
    snapshot_id: DurableId
    imported_at: str
    counts: dict[Kind, Count]


class Identity(StrictModel):
    record_id: DurableId
    revision_id: DurableId
    legacy_id: Text
    source_id: DurableId


class Provenance(StrictModel):
    mapping_id: DurableId
    source_id: DurableId
    role: Text
    legacy_id: Text
    # Heterogeneous original source observations are already validated by db_read.
    payload: dict[str, JsonValue]
    decision: Decision


class Record(Identity, Generic[T]):
    data: T


class EvidenceRecord(Record[T], Generic[T]):
    provenance: list[Provenance]


class TransactionRecord(Identity):
    bank_transaction: BankTransaction
    event: SavedEvent | None
    classification: EffectiveTransaction
    settlement_effect: ClassificationEffect | None
    relationships: list[Record[Relationship]]
    provenance: list[Provenance]


class AccountData(StrictModel):
    bank: Bank
    account: Text


class AccountRecord(EvidenceRecord[AccountData]):
    transaction_count: Count
    balance_after: SignedAmount | None
    balance_date: Day
    balance_transaction_id: TransactionId


class ObligationRecord(Identity):
    obligation: Obligation
    settlement: SettlementView
    allocations: list[Record[Allocation]]
    provenance: list[Provenance]


class Movements(StrictModel):
    debit: Amount
    credit: Amount


class BankMovements(Movements):
    net: SignedAmount


class CountedMovements(Movements):
    transaction_count: Count


class FinancialSummary(StrictModel):
    transaction_count: Count
    currency: Literal['INR']
    by_treatment: dict[EffectiveTreatment, Count]
    bank_movements: BankMovements
    recognized_income: Amount
    recognized_expense: Amount
    recognized_net: SignedAmount
    excluded_non_income_expense: CountedMovements
    uncertain: CountedMovements
    verified_principal_repayments_included_in_excluded: Amount
    income_expense_complete: bool


class CategoryRecord(StrictModel):
    category: str | None
    summary: FinancialSummary


class OutstandingSummary(StrictModel):
    obligation_count: Count
    by_status: dict[SettlementStatus, Count]
    confirmed_outstanding_subtotal: Amount
    total_outstanding: Amount | None
    unassessed_count: Count
    unknown_principal_count: Count


class ObligationSummary(StrictModel):
    receivable: OutstandingSummary
    payable: OutstandingSummary


class SummaryFilters(StrictModel):
    bank: Literal['kotak', 'sbi'] | None
    date_from: Day | None
    date_to: Day | None
    account_id: DurableId | None
    category: str | None
    treatment: EffectiveTreatment | None
    uncategorized: bool


class SummaryResult(StrictModel):
    schema_version: Literal['3D.3.1']
    snapshot_id: DurableId
    filters: SummaryFilters
    financial: FinancialSummary
    obligations: ObligationSummary


class Health(StrictModel):
    status: Literal['ok']
    mode: Literal['local-read-only']


class SnapshotQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    snapshot_id: UUID


class Filters(SnapshotQuery):
    bank: Literal['kotak', 'sbi'] | None = None
    account_id: UUID | None = None
    date_from: Day | None = None
    date_to: Day | None = None
    category: Text | None = None
    treatment: EffectiveTreatment | None = None
    uncategorized: bool = False

    @model_validator(mode='after')
    def valid_combination(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from must not be after date_to')
        if self.category is not None and self.uncategorized:
            raise ValueError('category and uncategorized are mutually exclusive')
        return self

    def read_filters(self):
        return self.model_dump(mode='json', exclude={'snapshot_id', 'offset', 'limit'})


class TransactionQuery(Filters):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class SettlementQuery(Filters):
    status: SettlementStatus | None = None


TransactionPage.model_rebuild()
