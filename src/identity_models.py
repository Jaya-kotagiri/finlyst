"""Strict sidecar contracts; existing financial documents keep their legacy IDs."""

from typing import Annotated, Literal

from pydantic import Field

from .event_models import StrictModel
from .output_models import Sha256, Text

DurableId = Annotated[str, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')]
Kind = Literal['account', 'transaction', 'event', 'relationship', 'obligation',
               'allocation', 'settlement', 'origin_question', 'candidate', 'settlement_question']


class Source(StrictModel):
    role: Text
    content_sha256: Sha256
    document_sha256: Sha256
    locations: list[Text]
    upstream_files: dict[Text, list[Sha256]]


class Record(StrictModel):
    kind: Kind
    birth_seed: Sha256
    identity_keys: Annotated[list[Sha256], Field(min_length=1)]


class Revision(StrictModel):
    record_id: DurableId
    interpretation_sha256: Sha256
    references: dict[Text, list[DurableId]]


class Continuity(StrictModel):
    # Null explicitly requests a distinct new record; it is not an unknown answer.
    target_id: DurableId | None
    actor: Text
    reason: Text


class Decision(StrictModel):
    status: Literal['resolved', 'unresolved']
    durable_id: DurableId | None
    revision_id: DurableId | None
    reason: Text
    candidate_ids: list[DurableId]
    references: dict[Text, list[DurableId]]
    unresolved_references: dict[Text, list[str]]
    continuity: Continuity | None


class LegacyMapping(StrictModel):
    source_id: DurableId
    kind: Kind
    legacy_id: Text
    record_sha256: Sha256
    identity_evidence: list[Sha256] = Field(default_factory=list)
    decisions: Annotated[list[Decision], Field(min_length=1)]


class Registry(StrictModel):
    schema_version: Literal['3R.2C.1']
    sources: dict[DurableId, Source]
    records: dict[DurableId, Record]
    revisions: dict[DurableId, Revision]
    legacy_mappings: dict[DurableId, LegacyMapping]
