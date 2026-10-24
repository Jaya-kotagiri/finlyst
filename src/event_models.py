"""Structured semantic output; null means unknown/not applicable, never guessed."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Amount = Annotated[str, Field(pattern=r'^\d+\.\d{2}$')]
Day = Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Entity(StrictModel):
    name: str
    role: str
    aliases: list[str]


class Component(StrictModel):
    """Shares, receivables, cash legs, obligations and intended future actions."""

    kind: str
    amount: Amount | None
    party: str | None
    status: Literal['completed', 'pending', 'planned', 'uncertain']
    description: str


class FinancialEvent(StrictModel):
    source_id: str
    event_type: str | None
    date: Day | None
    amount: Amount | None
    direction: Literal['debit', 'credit'] | None
    currency: str | None
    merchant: str | None
    payee: str | None
    payer: str | None
    category: str | None
    subcategory: str | None
    description: str | None
    purpose_context: str | None
    entities: list[Entity]
    components: list[Component]
    matching_clues: list[str]
    confidence: Literal['high', 'medium', 'low']
    ambiguities: list[str]
    additional_context: list[str]

    @field_validator('description', 'category', 'subcategory', 'purpose_context', 'event_type')
    @classmethod
    def unknown_is_null(cls, value):
        if value is not None and not value.strip():
            raise ValueError('use null for unknown meaning, not an empty string')
        return value

    @field_validator('date')
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value


class EventBatch(StrictModel):
    events: list[FinancialEvent]
