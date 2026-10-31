-- Phase 3D.2: retain coherent import membership and exact source observations.
-- No latest-pointer or financial interpretation is inferred by these tables.
CREATE TABLE finlyst.imported_snapshots (
    snapshot_id uuid PRIMARY KEY,
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    registry_schema_version text NOT NULL CHECK (btrim(registry_schema_version) <> ''),
    registry_payload jsonb NOT NULL CHECK (jsonb_typeof(registry_payload) = 'object'),
    imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((registry_payload ->> 'schema_version') IS NOT DISTINCT FROM registry_schema_version)
);

CREATE TABLE finlyst.snapshot_sources (
    snapshot_id uuid NOT NULL REFERENCES finlyst.imported_snapshots,
    role text NOT NULL CHECK (btrim(role) <> ''),
    source_id uuid NOT NULL REFERENCES finlyst.source_artifacts,
    PRIMARY KEY (snapshot_id, role)
);
CREATE INDEX snapshot_sources_source ON finlyst.snapshot_sources (source_id);

CREATE TABLE finlyst.snapshot_records (
    snapshot_id uuid NOT NULL REFERENCES finlyst.imported_snapshots,
    record_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL,
    PRIMARY KEY (snapshot_id, record_id),
    FOREIGN KEY (revision_id, record_id, record_kind)
        REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind)
);
CREATE INDEX snapshot_records_revision ON finlyst.snapshot_records (revision_id);
CREATE INDEX snapshot_records_record ON finlyst.snapshot_records (record_id);

CREATE TABLE finlyst.snapshot_decisions (
    snapshot_id uuid NOT NULL REFERENCES finlyst.imported_snapshots,
    mapping_id uuid NOT NULL,
    decision_order integer NOT NULL,
    PRIMARY KEY (snapshot_id, mapping_id),
    FOREIGN KEY (mapping_id, decision_order)
        REFERENCES finlyst.identity_decisions (mapping_id, decision_order)
);
CREATE INDEX snapshot_decisions_mapping ON finlyst.snapshot_decisions (mapping_id, decision_order);

CREATE TABLE finlyst.source_observations (
    mapping_id uuid PRIMARY KEY REFERENCES finlyst.legacy_mappings,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
);

-- A settlement revision names durable allocations. An allocation's evidence can
-- gain a revision without changing the parent settlement revision. Retain both
-- historical edges; coherent reads MUST select the allocation revision through
-- snapshot_records for the same snapshot, never sum all historical edges.
ALTER TABLE finlyst.settlement_allocations
    DROP CONSTRAINT settlement_allocations_pkey,
    ADD PRIMARY KEY (settlement_revision_id, allocation_record_id, allocation_revision_id);
