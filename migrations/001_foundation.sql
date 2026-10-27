-- Phase 3D.1: storage contracts only; no financial data or inferred facts.
CREATE SCHEMA finlyst;
CREATE DOMAIN finlyst.sha256 AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
-- Unconstrained NUMERIC avoids coercion/rounding before the precision check.
CREATE DOMAIN finlyst.money_amount AS numeric
    CHECK (VALUE NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
           AND VALUE = trunc(VALUE, 2));
CREATE DOMAIN finlyst.nonnegative_amount AS finlyst.money_amount CHECK (VALUE >= 0);
CREATE DOMAIN finlyst.positive_amount AS finlyst.money_amount CHECK (VALUE > 0);
CREATE DOMAIN finlyst.record_kind AS text CHECK (VALUE IN
    ('account','transaction','event','relationship','obligation','allocation','settlement',
     'origin_question','candidate','settlement_question'));
CREATE DOMAIN finlyst.confidence AS text CHECK (VALUE IN ('high','medium','low'));

CREATE TABLE finlyst.durable_records (
    record_id uuid PRIMARY KEY,
    record_kind finlyst.record_kind NOT NULL,
    birth_seed finlyst.sha256 NOT NULL,
    UNIQUE (record_id, record_kind),
    UNIQUE (record_kind, birth_seed)
);
CREATE TABLE finlyst.identity_keys (
    record_id uuid NOT NULL REFERENCES finlyst.durable_records,
    identity_key finlyst.sha256 NOT NULL,
    PRIMARY KEY (record_id, identity_key)
);
-- Evidence keys are intentionally NOT globally unique: ambiguity is legitimate.
CREATE INDEX identity_keys_lookup ON finlyst.identity_keys (identity_key);

CREATE TABLE finlyst.source_artifacts (
    source_id uuid PRIMARY KEY,
    role text NOT NULL CHECK (btrim(role) <> ''),
    content_sha256 finlyst.sha256 NOT NULL,
    document_sha256 finlyst.sha256
);
CREATE TABLE finlyst.source_documents (
    source_id uuid PRIMARY KEY REFERENCES finlyst.source_artifacts,
    schema_version text CHECK (schema_version IS NULL OR btrim(schema_version) <> ''),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) IN ('object','array'))
);
-- NULL schema_version is deliberate for the existing unversioned bank arrays.
CREATE TABLE finlyst.source_locations (
    source_id uuid NOT NULL REFERENCES finlyst.source_artifacts,
    location text NOT NULL CHECK (btrim(location) <> ''),
    PRIMARY KEY (source_id, location)
);
CREATE TABLE finlyst.source_dependencies (
    source_id uuid NOT NULL REFERENCES finlyst.source_artifacts,
    upstream_path text NOT NULL CHECK (btrim(upstream_path) <> ''),
    content_sha256 finlyst.sha256 NOT NULL,
    PRIMARY KEY (source_id, upstream_path, content_sha256)
);
CREATE TABLE finlyst.interpretation_revisions (
    revision_id uuid PRIMARY KEY,
    record_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL,
    interpretation_sha256 finlyst.sha256 NOT NULL,
    FOREIGN KEY (record_id, record_kind) REFERENCES finlyst.durable_records (record_id, record_kind),
    UNIQUE (revision_id, record_id, record_kind)
);
CREATE INDEX revisions_record ON finlyst.interpretation_revisions (record_id);
CREATE TABLE finlyst.revision_references (
    revision_id uuid NOT NULL REFERENCES finlyst.interpretation_revisions,
    role text NOT NULL CHECK (btrim(role) <> ''),
    target_record_id uuid NOT NULL REFERENCES finlyst.durable_records,
    PRIMARY KEY (revision_id, role, target_record_id)
);
CREATE INDEX revision_reference_target ON finlyst.revision_references (target_record_id);

CREATE TABLE finlyst.legacy_mappings (
    mapping_id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES finlyst.source_artifacts,
    record_kind finlyst.record_kind NOT NULL,
    legacy_id text NOT NULL CHECK (btrim(legacy_id) <> ''),
    record_sha256 finlyst.sha256 NOT NULL,
    UNIQUE (source_id, record_kind, legacy_id),
    UNIQUE (mapping_id, record_kind)
);
CREATE TABLE finlyst.mapping_evidence (
    mapping_id uuid NOT NULL REFERENCES finlyst.legacy_mappings,
    identity_key finlyst.sha256 NOT NULL,
    PRIMARY KEY (mapping_id, identity_key)
);
CREATE TABLE finlyst.identity_decisions (
    mapping_id uuid NOT NULL,
    decision_order integer NOT NULL CHECK (decision_order > 0),
    record_kind finlyst.record_kind NOT NULL,
    status text NOT NULL CHECK (status IN ('resolved','unresolved')),
    record_id uuid,
    revision_id uuid,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    unresolved_references jsonb NOT NULL CHECK (jsonb_typeof(unresolved_references) = 'object'),
    continuity jsonb CHECK (continuity IS NULL OR jsonb_typeof(continuity) = 'object'),
    PRIMARY KEY (mapping_id, decision_order),
    UNIQUE (mapping_id, decision_order, record_kind),
    FOREIGN KEY (mapping_id, record_kind) REFERENCES finlyst.legacy_mappings (mapping_id, record_kind),
    FOREIGN KEY (revision_id, record_id, record_kind)
        REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind),
    CHECK ((status = 'resolved' AND record_id IS NOT NULL AND revision_id IS NOT NULL
             AND unresolved_references = '{}'::jsonb)
        OR (status = 'unresolved' AND record_id IS NULL AND revision_id IS NULL))
);
CREATE INDEX decisions_record ON finlyst.identity_decisions (record_id);
CREATE INDEX decisions_revision ON finlyst.identity_decisions (revision_id);
CREATE TABLE finlyst.decision_references (
    mapping_id uuid NOT NULL,
    decision_order integer NOT NULL,
    role text NOT NULL CHECK (btrim(role) <> ''),
    target_record_id uuid NOT NULL REFERENCES finlyst.durable_records,
    PRIMARY KEY (mapping_id, decision_order, role, target_record_id),
    FOREIGN KEY (mapping_id, decision_order) REFERENCES finlyst.identity_decisions
);
CREATE INDEX decision_reference_target ON finlyst.decision_references (target_record_id);
CREATE TABLE finlyst.decision_candidates (
    mapping_id uuid NOT NULL,
    decision_order integer NOT NULL,
    candidate_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL,
    PRIMARY KEY (mapping_id, decision_order, candidate_id),
    FOREIGN KEY (mapping_id, decision_order, record_kind)
        REFERENCES finlyst.identity_decisions (mapping_id, decision_order, record_kind),
    FOREIGN KEY (candidate_id, record_kind) REFERENCES finlyst.durable_records (record_id, record_kind)
);
CREATE INDEX decision_candidate_target ON finlyst.decision_candidates (candidate_id);

-- Typed tables store revisions, not a mutable replacement of the permanent record.
CREATE TABLE finlyst.accounts (
    revision_id uuid PRIMARY KEY,
    record_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL DEFAULT 'account' CHECK (record_kind = 'account'),
    bank text NOT NULL CHECK (bank ~ '^[A-Z][A-Z0-9_]*$'),
    account_number text CHECK (account_number IS NULL OR btrim(account_number) <> ''),
    FOREIGN KEY (revision_id, record_id, record_kind) REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind)
);
CREATE INDEX accounts_record ON finlyst.accounts (record_id);
CREATE TABLE finlyst.bank_transactions (
    revision_id uuid PRIMARY KEY,
    record_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL DEFAULT 'transaction' CHECK (record_kind = 'transaction'),
    account_id uuid,
    account_kind finlyst.record_kind NOT NULL DEFAULT 'account' CHECK (account_kind = 'account'),
    bank text NOT NULL CHECK (bank ~ '^[A-Z][A-Z0-9_]*$'),
    transaction_date date NOT NULL,
    amount finlyst.nonnegative_amount NOT NULL,
    direction text NOT NULL CHECK (direction IN ('debit','credit')),
    raw_description text NOT NULL CHECK (btrim(raw_description) <> ''),
    reference text CHECK (reference IS NULL OR btrim(reference) <> ''),
    balance_after finlyst.money_amount,
    FOREIGN KEY (revision_id, record_id, record_kind) REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind),
    FOREIGN KEY (account_id, account_kind) REFERENCES finlyst.durable_records (record_id, record_kind)
);
CREATE INDEX bank_transactions_record ON finlyst.bank_transactions (record_id);
CREATE INDEX bank_transactions_account_date ON finlyst.bank_transactions (account_id, transaction_date);
CREATE INDEX bank_transactions_reference ON finlyst.bank_transactions (reference) WHERE reference IS NOT NULL;

CREATE TABLE finlyst.semantic_events (
    revision_id uuid PRIMARY KEY,
    record_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL DEFAULT 'event' CHECK (record_kind = 'event'),
    transaction_id uuid,
    transaction_kind finlyst.record_kind NOT NULL DEFAULT 'transaction' CHECK (transaction_kind = 'transaction'),
    event_type text,
    event_date date,
    amount finlyst.nonnegative_amount,
    direction text CHECK (direction IN ('debit','credit')),
    currency text,
    merchant text, payee text, payer text, category text, subcategory text,
    description text, purpose_context text,
    confidence finlyst.confidence NOT NULL,
    context jsonb NOT NULL CHECK (jsonb_typeof(context) = 'object'),
    FOREIGN KEY (revision_id, record_id, record_kind) REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind),
    FOREIGN KEY (transaction_id, transaction_kind) REFERENCES finlyst.durable_records (record_id, record_kind)
);
CREATE INDEX events_record ON finlyst.semantic_events (record_id);
CREATE INDEX events_transaction ON finlyst.semantic_events (transaction_id);

CREATE TABLE finlyst.relationships (
    revision_id uuid PRIMARY KEY,
    record_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL DEFAULT 'relationship' CHECK (record_kind = 'relationship'),
    relationship_type text NOT NULL CHECK (relationship_type IN ('self_transfer','cash_conversion','loan_advance',
        'loan_borrowing','reimbursement','reimbursement_expected','planned_transfer','payment_on_behalf',
        'purpose_specific_funding','possible_loan_return')),
    status text NOT NULL CHECK (status IN ('confirmed','identified','source_asserted','planned','unresolved')),
    rule text NOT NULL, confidence finlyst.confidence NOT NULL, purpose text,
    financial_treatment jsonb NOT NULL CHECK (jsonb_typeof(financial_treatment) = 'object'),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array'),
    details jsonb NOT NULL CHECK (jsonb_typeof(details) = 'object'),
    FOREIGN KEY (revision_id, record_id, record_kind) REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind)
);
CREATE INDEX relationships_record ON finlyst.relationships (record_id);
CREATE TABLE finlyst.relationship_transactions (
    relationship_revision_id uuid NOT NULL REFERENCES finlyst.relationships (revision_id),
    transaction_id uuid NOT NULL,
    transaction_kind finlyst.record_kind NOT NULL DEFAULT 'transaction' CHECK (transaction_kind = 'transaction'),
    role text NOT NULL CHECK (role IN ('participant','candidate')),
    PRIMARY KEY (relationship_revision_id, transaction_id, role),
    FOREIGN KEY (transaction_id, transaction_kind) REFERENCES finlyst.durable_records (record_id, record_kind)
);
CREATE INDEX relationship_transactions_target ON finlyst.relationship_transactions (transaction_id);

CREATE TABLE finlyst.effective_classifications (
    source_document_id uuid NOT NULL REFERENCES finlyst.source_documents (source_id),
    transaction_id uuid NOT NULL,
    transaction_kind finlyst.record_kind NOT NULL DEFAULT 'transaction' CHECK (transaction_kind = 'transaction'),
    event_id uuid,
    event_kind finlyst.record_kind NOT NULL DEFAULT 'event' CHECK (event_kind = 'event'),
    treatment text NOT NULL CHECK (treatment IN ('self_transfer','cash_conversion','loan_advance','loan_borrowing',
        'reimbursement_received','recoverable_or_shared_expense','salary_income','unknown','expense')),
    income boolean, expense boolean,
    status text NOT NULL CHECK (status IN ('unknown','supported','allocation_deferred')),
    classification_source text NOT NULL CHECK (classification_source IN
        ('insufficient_evidence','financial_relationships','bank_facts','matched_semantics')),
    rule text NOT NULL, confidence finlyst.confidence NOT NULL,
    semantic jsonb CHECK (semantic IS NULL OR jsonb_typeof(semantic) = 'object'),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    PRIMARY KEY (source_document_id, transaction_id),
    FOREIGN KEY (transaction_id, transaction_kind) REFERENCES finlyst.durable_records (record_id, record_kind),
    FOREIGN KEY (event_id, event_kind) REFERENCES finlyst.durable_records (record_id, record_kind)
);
CREATE INDEX classifications_transaction ON finlyst.effective_classifications (transaction_id);
CREATE INDEX classifications_event ON finlyst.effective_classifications (event_id);
CREATE TABLE finlyst.classification_relationships (
    source_document_id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    relationship_id uuid NOT NULL,
    relationship_kind finlyst.record_kind NOT NULL DEFAULT 'relationship' CHECK (relationship_kind = 'relationship'),
    role text NOT NULL CHECK (role IN ('participant','candidate')),
    PRIMARY KEY (source_document_id, transaction_id, relationship_id, role),
    FOREIGN KEY (source_document_id, transaction_id) REFERENCES finlyst.effective_classifications,
    FOREIGN KEY (relationship_id, relationship_kind) REFERENCES finlyst.durable_records (record_id, record_kind)
);
CREATE INDEX classification_relationships_target ON finlyst.classification_relationships (relationship_id);

CREATE TABLE finlyst.obligations (
    revision_id uuid PRIMARY KEY,
    record_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL DEFAULT 'obligation' CHECK (record_kind = 'obligation'),
    obligation_type text NOT NULL CHECK (obligation_type IN ('loan_advance','loan_borrowing','reimbursement','explicit_payable')),
    direction text NOT NULL CHECK (direction IN ('receivable','payable')),
    counterparty text, purpose text, original_amount finlyst.positive_amount,
    currency text NOT NULL CHECK (currency = 'INR'),
    currency_basis text NOT NULL CHECK (currency_basis = 'owned_INR_bank_statement_scope'),
    origin_date date NOT NULL,
    origin_status text NOT NULL CHECK (origin_status = 'CONFIRMED'),
    settlement_status text NOT NULL CHECK (settlement_status = 'UNASSESSED'),
    requires_clarification boolean NOT NULL,
    confidence finlyst.confidence NOT NULL,
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    FOREIGN KEY (revision_id, record_id, record_kind) REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind)
);
CREATE INDEX obligations_record ON finlyst.obligations (record_id);
CREATE TABLE finlyst.obligation_links (
    obligation_revision_id uuid NOT NULL REFERENCES finlyst.obligations (revision_id),
    role text NOT NULL CHECK (role IN ('origin','event','supporting_relationship','context_relationship')),
    target_record_id uuid NOT NULL,
    target_kind finlyst.record_kind NOT NULL,
    PRIMARY KEY (obligation_revision_id, role, target_record_id),
    CHECK ((role = 'origin' AND target_kind = 'transaction') OR (role = 'event' AND target_kind = 'event')
        OR (role IN ('supporting_relationship','context_relationship') AND target_kind = 'relationship')),
    FOREIGN KEY (target_record_id, target_kind) REFERENCES finlyst.durable_records (record_id, record_kind)
);
CREATE INDEX obligation_links_target ON finlyst.obligation_links (target_record_id);

CREATE TABLE finlyst.settlements (
    revision_id uuid PRIMARY KEY,
    record_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL DEFAULT 'settlement' CHECK (record_kind = 'settlement'),
    obligation_id uuid NOT NULL,
    obligation_kind finlyst.record_kind NOT NULL DEFAULT 'obligation' CHECK (obligation_kind = 'obligation'),
    direction text NOT NULL CHECK (direction IN ('receivable','payable')),
    currency text NOT NULL CHECK (currency = 'INR'),
    original_principal finlyst.positive_amount,
    verified_repayments finlyst.nonnegative_amount NOT NULL,
    verified_zero_repayments boolean NOT NULL,
    adjustment finlyst.money_amount NOT NULL,
    unadjusted_residual finlyst.money_amount,
    outstanding_amount finlyst.nonnegative_amount,
    status text NOT NULL CHECK (status IN ('OPEN','PARTIAL','CLOSED','UNASSESSED')),
    as_of date NOT NULL, reason text NOT NULL,
    evidence_strength text CHECK (evidence_strength = 'high'),
    confirmation_methods text[] NOT NULL CHECK (confirmation_methods <@ ARRAY['inferred','source_supported','explicit_source_allocation']::text[]),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    CHECK ((status = 'UNASSESSED') = (outstanding_amount IS NULL)),
    CHECK (original_principal IS NOT NULL OR status = 'UNASSESSED'),
    CHECK (status <> 'CLOSED' OR outstanding_amount = 0),
    FOREIGN KEY (revision_id, record_id, record_kind) REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind),
    FOREIGN KEY (obligation_id, obligation_kind) REFERENCES finlyst.durable_records (record_id, record_kind),
    UNIQUE (revision_id, obligation_id)
);
CREATE INDEX settlements_record ON finlyst.settlements (record_id);
CREATE INDEX settlements_obligation ON finlyst.settlements (obligation_id);
CREATE TABLE finlyst.allocations (
    revision_id uuid PRIMARY KEY,
    record_id uuid NOT NULL,
    record_kind finlyst.record_kind NOT NULL DEFAULT 'allocation' CHECK (record_kind = 'allocation'),
    obligation_id uuid NOT NULL,
    obligation_kind finlyst.record_kind NOT NULL DEFAULT 'obligation' CHECK (obligation_kind = 'obligation'),
    repayment_transaction_id uuid NOT NULL,
    transaction_kind finlyst.record_kind NOT NULL DEFAULT 'transaction' CHECK (transaction_kind = 'transaction'),
    amount finlyst.positive_amount NOT NULL,
    currency text NOT NULL CHECK (currency = 'INR'),
    rule text NOT NULL,
    explicit_split boolean NOT NULL, full_settlement_intent boolean NOT NULL, explicit_minor_overpayment boolean NOT NULL,
    confidence text NOT NULL CHECK (confidence = 'high'),
    evidence_strength text NOT NULL CHECK (evidence_strength = 'high'),
    confirmation_method text NOT NULL CHECK (confirmation_method IN ('inferred','source_supported','explicit_source_allocation')),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    FOREIGN KEY (revision_id, record_id, record_kind) REFERENCES finlyst.interpretation_revisions (revision_id, record_id, record_kind),
    FOREIGN KEY (obligation_id, obligation_kind) REFERENCES finlyst.durable_records (record_id, record_kind),
    FOREIGN KEY (repayment_transaction_id, transaction_kind) REFERENCES finlyst.durable_records (record_id, record_kind),
    UNIQUE (revision_id, record_id, obligation_id)
);
CREATE INDEX allocations_record ON finlyst.allocations (record_id);
CREATE INDEX allocations_obligation ON finlyst.allocations (obligation_id);
CREATE INDEX allocations_repayment ON finlyst.allocations (repayment_transaction_id);
CREATE TABLE finlyst.settlement_allocations (
    settlement_revision_id uuid NOT NULL,
    allocation_revision_id uuid NOT NULL,
    allocation_record_id uuid NOT NULL,
    obligation_id uuid NOT NULL,
    PRIMARY KEY (settlement_revision_id, allocation_record_id),
    FOREIGN KEY (settlement_revision_id, obligation_id) REFERENCES finlyst.settlements (revision_id, obligation_id),
    FOREIGN KEY (allocation_revision_id, allocation_record_id, obligation_id)
        REFERENCES finlyst.allocations (revision_id, record_id, obligation_id)
);
CREATE INDEX settlement_allocations_allocation ON finlyst.settlement_allocations (allocation_revision_id);
