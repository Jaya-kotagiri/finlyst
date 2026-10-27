# Project state

## Phase 3D.1 complete: PostgreSQL foundation

Added `src/db.py`, `src/local_postgres.py`, `migrations/001_foundation.sql`,
`tests/test_db.py` and `tests/run_database_tests.py`. Added the pinned Psycopg 3 binary
adapter (`psycopg[binary]==3.3.6`); no ORM or migration framework. Existing financial
modules, Pydantic contracts, durable-ID generation and unified JSON CLI are unchanged.

Installed the official PostgreSQL **18.6** Windows binaries under ignored
`.local/postgres/runtime/`, initialized a project-local cluster and created the empty
`finlyst` database. It listens only on **127.0.0.1:55432**, uses SCRAM authentication,
and runs as an explicitly managed background process, not an installed Windows service.
The application/migration owner is non-superuser with no CREATEDB or CREATEROLE privileges.
Generated credentials are stored only in ignored `.env.db` and `.local/postgres/admin.json`;
existing API environment configuration was not read/changed for database operations.
Environment-based FINLYST_DB_* settings override the private database configuration.

Migration 001 is applied, with no pending migrations. The 25-table `finlyst` schema
stores permanent UUIDs, birth-seed identity metadata, separate interpretation revisions,
source artifacts/full JSON/schema versions, source-scoped legacy aliases and decision
history. Typed revision tables cover accounts, bank facts, events, relationships, effective
classifications, obligations, settlements and allocations, with relational participant and
provenance links. All ten registry kinds remain representable. Composite foreign keys
check record kinds and revision ownership; evidence hashes are not forced globally unique.

Money uses exact checked NUMERIC domains, with no silent cent rounding or nonfinite values.
Unknown principals/flags stay nullable; UNASSESSED outstanding remains null, not zero.
CLOSED requires known zero, and inferred confirmation remains distinct from high evidence
strength. Full source JSON retains nested evidence and local correction history. Future
import must select coherent source/revision snapshots and retain existing financial validation;
this slice does not implement import, active-version selection or new financial calculations.

Migrations are versioned SQL with transactional DDL/history, an advisory lock and recorded
checksums normalized for Git line endings. Repeat application is a no-op; modified applied
files are rejected. A failed synthetic migration rolled back both DDL and ledger changes.
The local setup also passed stop/start and repeat-init checks. A Windows detached-process
pipe-handle issue found during initial startup was corrected in the helper before verification.

Verification completed on 2026-10-27:

- **239 tests passed with PostgreSQL integration enabled: 214 existing + 25 new; no skips.**
  New coverage includes environment precedence/redacted errors, migrations/checksum drift,
  rollback, exact money, null/UNASSESSED/CLOSED contracts, UUID/type/revision foreign keys,
  relationships/allocations, source scopes, corrections across revisions, provenance and
  compatibility with controlled values in the existing Pydantic models.
- Integration tests used a freshly created disposable database, synthetic records only,
  per-test rollbacks and cleanup. **No test databases remain. All 25 application tables
  have zero rows.** Only the separate migration ledger contains its version record.
- All **20 protected files** match `tmp/phase3d1/baseline.json`: seven private inputs,
  twelve financial JSON outputs and the identity registry. Their inventory is unchanged.
  Existing offline validation still passes **14 integrity checks and 844 identity mappings**.
  The ignored verification report is `tmp/phase3d1/verification.json`.
- Existing input/output/environment ignore rules are preserved; `.local/` is additionally
  ignored. Credentials, server binaries/data/logs and generated financial data are excluded
  from Git. README documents local setup, configuration, migrations and integration testing.

**No financial import, API call, financial-rule change or dashboard integration.
Stop before Phase 3D.2.** The local database foundation is ready for review.

## Phase 3R.3 complete: unified pipeline CLI

Added `src/cli.py` using standard-library argparse and existing Python stage functions.
The sequential order is bank, saved events, reconciliation, relationships, classification,
coverage audit, obligations, settlements, durable identities and integrity. Every stage
has an individual command; `run --from ... --to ...` selects an inclusive range. `--root`
configures the project directory, and bank/events/reconcile support per-bank selection.
README documents the requested commands, paths, API policy and failure behavior.

Offline is the default. Saved events are checked against normalized bank facts and
current notes. Missing events require explicit `--allow-api` on run/events; this only
permits generation of missing outputs, never automatic replacement of existing events.
The existing generation processor, diagnostics and zero-retry API client are reused.
No real API calls were made during implementation or verification.

Added keyword-only `write_output=False` support to the existing integrity and identity
runners, leaving their default behavior and calculations unchanged. `validate --all`
uses those read-only paths, validates the saved reports/mappings and rejects stale or
missing outputs without repairing them. Help exits before stage imports or project
access. Errors identify the failing stage and return nonzero; later stages do not run.
Completed earlier stages are not rolled back. No dependency or framework was added.

Verification on 2026-10-27:

- **214 tests passed: all 192 existing tests plus 22 CLI regressions.** Tests cover
  ordering, ranges, bank/root options, invalid arguments, lazy help, offline safety,
  mocked explicit API access without retries, stale inputs, failure handling, synthetic
  end-to-end reruns, read-only success/failure and missing durable mapping detection.
- Executed all five requested command forms on actual data, plus a second full run
  without `--offline` to verify the default. Both complete runs and the selected-range
  run succeeded. Validation passed all **14 integrity checks and 844 identity mappings**.
- Captured the pre-change inventory/hashes in `tmp/phase3r3/baseline.json`. After every
  command, all **20 protected files** remained byte-identical: seven private source
  files, twelve existing financial JSON outputs and the durable identity registry.
  Help and read-only validation also preserved protected-file modification times;
  no input/output files were added or removed. Verification is retained under ignored
  `tmp/phase3r3/verification.json`.
- Legacy IDs, permanent UUIDs, classification, settlement evidence and all unresolved
  financial states are unchanged. The existing integrity runner's dataset-specific
  assertions remain in force; supporting a different baseline is a separate change.

No financial-rule changes, PostgreSQL implementation or unapproved API calls.
**Stop after Phase 3R.3 verification, before Phase 3D.**

## Phase 3R.2C complete: durable financial identities

Audited transaction, semantic event, relationship, obligation, allocation, settlement
and clarification IDs. Existing sequence IDs are file-scoped; several downstream hashes
depend on mutable status, rule or counterparty names. Added `src/identities.py`, strict
`src/identity_models.py`, `tests/test_identities.py` and `DURABLE_IDS.md`. No existing
production module, financial schema, calculation, legacy ID or source test was changed.

The new private `output/identities/identity_registry.json` (`3R.2C.1`) explicitly maps
source-scoped legacy IDs to permanent UUIDs, separates interpretation revisions from
source provenance, and records durable cross-record references. Existing financial
files retain their original formats and IDs. Exact supported overlaps reuse bank IDs
across renumbered imports; missing account evidence, indistinguishable occurrences,
conflicting facts and uncertain claim continuity stay unresolved rather than creating
speculative duplicate money. Counterparty corrections can retain the original claim
through an explicit identity decision with actor/reason provenance. No such manual
decisions were needed or invented for the current data.

Current registry: **154 transactions, 85 events, 27 relationships, eight obligations,
five allocations and eight settlement views**. It also maps two accounts, six origin
questions, one settlement candidate and one settlement question: **297 records,
297 revisions, 11 source artifacts and 844 scoped legacy mappings; zero unresolved
identity mappings**. Existing financial ambiguity and UNASSESSED statuses are unchanged.

Verification completed on 2026-10-27:

- **192 tests passed: all 165 unchanged existing tests plus 27 identity regressions.**
  Coverage includes repeat/overlap/reversed imports, account/month separation, sequence
  renumbering, indistinguishable rows and subsets, explicit continuity, counterparty
  corrections, status/rule changes, shared claims, foreign references and malformed
  registry/input rejection before overwriting a valid registry.
- Actual-data migration validates existing stage contracts and deterministically
  replays reconciliation validation, relationships, classification, obligations and
  settlements. Repeated migrations produce byte-identical registry output.
- All **12 existing financial JSON files remain byte-identical** to the retained
  original baseline. Hash/inventory checks confirm all **19 original input/output
  files unchanged**, including the seven private source files. The registry is the
  only additional output. Baseline and verification evidence are under ignored
  `tmp/phase3r2c/`; byte comparison also uses the retained 3R.2B baseline copies.
- Existing tracked production/test/dependency/ignore files are unchanged. No new
  dependency, API call, CLI, financial-rule change or PostgreSQL implementation.

Operational limits: retain and privately back up the registry and source snapshots;
derived UUIDs are allocated once and are not regenerated from each interpretation.
The current JSON pipeline remains one document per bank per snapshot. Additional
account/month snapshots enter the same registry separately through the Python API;
consumers must deduplicate through durable IDs rather than concatenate legacy rows.
Automatic account identity requires the full unmasked account number supported by
these sources. Multi-writer transactional persistence remains for 3D. PostgreSQL key,
revision, provenance and unresolved-identity design is documented, not implemented.

### Resumed checkpoint verification (2026-10-27)

Resumed from clean, committed checkpoint `956eaf5`; no unfinished implementation or
uncommitted schema work was present. Preserved the implementation and reran all
**192 tests successfully**, including overlap, identity stability, ambiguity and
cross-record mapping regressions. The original 165 tests and pre-3R.2C production
files were compared against `5fab5f3` and remain unchanged.

Compared all 19 protected files against the unchanged `tmp/phase3r2c/baseline.json`:
all hashes match, and all 12 existing financial outputs also match retained baseline
bytes. Two actual-data registry runs leave the registry byte-identical to the resumed
checkpoint, with the same 297 records, 297 revisions and 844 valid mappings. No IDs
were reallocated and no financial files were changed. Updated the ignored verification
helper to identify original tests from the fixed pre-identity commit rather than the
current Git index; saved refreshed verification evidence under `tmp/phase3r2c/`.
No production repair or duplicated implementation was necessary.

**Stop before Phase 3R.3; no subsequent slice started.** Earlier sections below describe
previous checkpoints.

## Phase 3R.2B complete: strict output schemas

Added `src/output_models.py` with reusable strict Pydantic contracts for reconciliation, relationships, effective classification, obligation origins and settlements. Supporting contracts cover normalized bank records, saved event envelopes (including approved local-correction history) and the coverage audit consumed by obligations. Existing versions remain unchanged: events `1.0`, reconciliation `3A.2`, relationships `3B.1`, classification `3C.1.1`, coverage `3C.1A.1`, obligations `3C.2.1` and settlements `3C.3.2`.

Added `src/schema_validation.py` for non-mutating validation and contextual JSON errors. File runners validate loaded documents before calculations/writes; builders and existing validators validate generated structures. Controlled treatments, types, statuses, rules and confirmation methods use Literals. Nested evidence, references, IDs, fingerprints, dates, decimal strings, strict booleans and counts are checked; extra fields, missing required fields and unsupported versions are rejected. File-level obligation/settlement contracts require verification metadata, while pure builders may omit it. Existing financial invariants, replay checks and calculations remain in place.

Validation returns the original data; model defaults/coercions are never written into it. Legitimate null semantics, unknown principals, unresolved candidates, UNASSESSED/null outstanding balances, signed adjustments and original correction history remain supported. Free-form semantic descriptions/categories are not forced into financial enums. No legacy values were reinterpreted and no existing saved output required correction.

Verification completed on 2026-10-27:

- **165 tests passed: all 142 existing tests plus 23 schema/boundary regressions.** Original test methods and assertion ASTs are preserved; synthetic envelopes were completed with required version/provenance fields.
- Negative tests cover invalid versions, IDs, controlled values, field/container types, dates, monetary precision, boolean coercion, missing/extra nested fields, fingerprints and lifecycle/null consistency. Malformed JSON, missing/unsupported versions and wrong document containers were tested at all five loaded stage boundaries; existing outputs and all fixture files remained unchanged after rejection. Invalid bank records also cannot overwrite outputs.
- All **154 bank transactions, 85 semantic events, 27 relationships, eight obligation origins and five repayment allocations** validate. The financial integrity audit passed **14 checks**, with zero failures.
- Regenerated bank, reconciliation, relationship, classification, coverage-audit, obligation, settlement and integrity outputs using existing runners. **All 12 output JSON files are byte-identical to the original retained baseline**: ten regenerated files plus the two existing event files, which were validated/reused without API calls. Comparison preserves existing Windows CRLF, key order and UTF-8 formatting.
- SHA-256 and inventory checks confirm **all 19 input/output files unchanged**, including all seven private source files. The original baseline and verification summary remain under ignored `tmp/phase3r2b/`.

No financial-rule changes, ID redesign, output-version/format changes, new dependencies, CLI changes, API calls or PostgreSQL work. **Stop before Phase 3R.2C (durable IDs).**

## Phase 3R.2A complete: reproducible tests

The read-only 3R.1 architecture audit found that 36 tests depended on an untracked `tests/fixtures/` directory, shared builders were imported from other test modules, and default discovery/individual-module imports were unreliable. This checkpoint repairs only test portability.

Added `tests/__init__.py` and `tests/support.py`. Shared synthetic relationship, semantic, obligation and settlement builders now live in test support, with their existing behavior preserved. All ten test modules use self-cleaning temporary workspaces under the existing `tests/` directory; CSV/XLSX/JSON fixtures are generated during tests. No pre-existing fixture directory, private input/output, `.env` or API key is needed. Test modules no longer import one another. README documents full discovery and individual-module commands.

Verification on Python 3.13 with all four dependency versions matching `requirements.txt`:

- Workspace: `python -B -m unittest discover -s tests -q` passed all **142 existing tests**.
- Clean detached Git checkout under `D:\finlyst\tmp\phase3r2a\checkout`, containing only tracked files and no `input/`, `output/`, `tests/fixtures/` or `.env`: default `python -B -m unittest discover -q` passed **142 tests**.
- Each of the **10 test modules passed independently in a fresh Python process**, totaling the same 142 tests.
- A shuffled suite (seed `20261025`) passed **142 tests**. The clean checkout remained clean afterward, with no leftover test directories or private-data dependencies.
- Used the existing virtual-environment interpreter and verified its pinned dependency versions; this was a clean source checkout, not a newly installed dependency environment. The sandbox initially denied temporary-file creation in the detached worktree; rerunning with filesystem approval passed without code changes.
- Compared test methods and assertion ASTs against the previous commit: all 142 methods and their assertions remain intact. No new tests or production changes were needed.
- SHA-256 and file-inventory comparisons confirm **36 protected production/source/output artifacts remain unchanged**, including all `src/` files, `app.py`, dependency/ignore files, private inputs and financial outputs. Schemas, IDs and financial behavior were not modified.

No API calls, financial refactoring, production CLI changes or database work. **Stop after Phase 3R.2A verification.** The other 3R.1 architecture recommendations remain pending.

## Phase 3C.4 complete: financial integrity audit

Added `src/integrity.py`, `tests/test_integrity.py` and private `output/audit/financial_integrity.json`. The read-only audit inspects every current bank, event, 3A, 3B, classification, coverage, obligation and settlement document; reparses original Kotak CSV/SBI XLSX and both notes workbooks; replays all deterministic stages and independently checks the repayment ledger. **14 checks passed, zero failed; full suite 142 tests passed (17 new integrity regressions/cross-layer tests).**

Coverage remains exactly **154 unique bank transactions and 85 preserved semantic events**. All 152 available adjacent bank balances reconcile with Decimal arithmetic; there are no duplicate normalized bank rows. The first opening balance is not independently asserted by this adjacent-balance test. All 85 supplied notes rows were processed; the 69 missing workbook rows are incomplete source coverage, not pipeline failure. Classification retains all original meanings and bank facts. Ordinary expenses remain expenses; transfers, cash conversions, reimbursements and advances are not counted as new income/spending. The settlement overlay does not create additional cashflows.

Verified: 27 source-supported relationships without duplicate links or invented self-transfers; eight unique obligation origins; five unique bank repayment allocations totaling 3,345.00; repayment capacities, direction/date support, reference hashes and exact balances; three CLOSED and five UNASSESSED obligations. Confirmed outstanding remains 0.00 in each direction, while 3,538.00 known ticket principal and three unknown principals remain unassessed. Unknown balances stay null, not zero. Train receipt SBI_0023 remains unallocated. Group-meal receipts do not establish original principal or closure. All four annotated financial-term questions and original clarification references remain intact.

Dad's SBI_0025 -> SBI_0026 remains CLOSED for exactly 3,000.00 with no adjustment. Settlement schema is now **3C.3.2**: allocations separately store `evidence_strength` and `confirmation_method`. Dad is `high` / `inferred`, never explicitly confirmed by a user or bank. Other methods are `source_supported` and `explicit_source_allocation`; these describe the source of support, not user approval. Existing `confidence` is retained for compatibility. The obligation settlement view carries `repayment_evidence_strength` and `confirmation_methods`, independently of lifecycle status and unknown principal.

Three deterministic defects were reproduced with failing synthetic tests and corrected:

1. A payment note claiming full settlement could trigger a small write-off despite its dated follow-up explicitly confirming that the residual was still owed. The dated positive balance now blocks tolerance, retaining PARTIAL and the actual residual.
2. Full-settlement language for one explicit split could be applied to all origins in the same receipt. It is now scoped to that origin's semicolon-delimited allocation clause; only explicit full settlement of all listed obligations applies globally. Unsupported scopes remain conservative.
3. Obligation/settlement source fingerprints included all files under `output/audit/`, which would make downstream reports affect upstream outputs and create a hash dependency cycle. Only the actual input `coverage_audit.json` is now fingerprinted as an audit dependency. Downstream audit reports cannot change origin/settlement bytes.

The real financial matches, IDs, amounts, statuses and totals did not change. Only `output/settlements/settlements.json` was regenerated to add evidence-method metadata/schema version; all 17 original inputs and earlier-stage files remain byte-identical to the pre-audit baseline. The integrity report fingerprints all 18 source/upstream artifacts including the updated settlement document. Repeated integrity runs produce identical bytes; rerunning settlements after the integrity report exists also produces identical bytes. Reversed-document-order stage replays agree with saved outputs.

The corruption tests additionally detect duplicate bank rows/relationships/allocations, discontinuous balances, changed or missing meanings, incorrect financial flags, repayment over-allocation, lost classification overlays, altered principals, false confirmation metadata and UNASSESSED incorrectly represented as a confirmed zero balance. Existing tests cover the inclusive 5.00 final-residual boundary, 5.01 rejection, multiple installments, unsupported full-settlement claims and unexplained overpayment.

**Ready for PostgreSQL migration of this evidence model, with unresolved financial questions preserved.** Migration must retain nullable amounts, UNASSESSED vs confirmed zero, original records and provenance, unique IDs/capacity constraints, separate evidence strength/confirmation method and the settlement classification overlay. This is not a claim of complete financial history or resolved debt. No new dependencies, AI calls, ML, database implementation, frontend, dashboard or assistant. **Stop before Phase 3D.**

## Phase 3C.3 complete: deterministic settlement engine

Added `src/settlements.py` and `tests/test_settlements.py`. Generated private `output/settlements/settlements.json`, containing repayment allocations, an updated obligation settlement view, unresolved candidates, clarification references, classification effects and evidence/source hashes. The original 3C.2 obligation-origin document remains unchanged. Earlier sections below describe historical slices.

| Status | Obligations |
| --- | ---: |
| OPEN | 0 |
| PARTIAL | 0 |
| CLOSED | 3 |
| UNASSESSED | 5 |

Five verified repayment allocations total **INR 3,345.00** (receivables 3,345.00; payables 0.00), with **0.00 rounding adjustments** on current data:

- Jeevin's KOTAK_0014 share: 40.00 received in KOTAK_0015; CLOSED.
- Manasa's KOTAK_0014 share: 25.00 received in SBI_0019; CLOSED, preserving cross-account context.
- Dad's SBI_0025 temporary advance: 3,000.00 received in SBI_0026; CLOSED. The new generic rule requires the source promise to return immediately, an exact bank counterparty key, same-day chronological order, equal full principal, no competing obligation and no indistinguishable competing return credit. SBI_0026 has no note; amount similarity alone did not establish this match.
- KOTAK_0032 group meal: Deena's 100.00 receipt in KOTAK_0033 and Manasa's 180.00 receipt in SBI_0022 are verified through existing 3B links. Both original principals remain null and settlement statuses UNASSESSED. Receiving those amounts does not establish the original shares or closure.

**Confirmed outstanding receivables: 0.00; confirmed outstanding payables: 0.00. This is not a claim that no money is owed.** The two parents' ticket obligations have 3,538.00 of known original principal with unassessed settlement; their current outstanding amount is null. SBI_0023 is an actual 3,500.00 reimbursement with an unresolved allocation between them, retained as one candidate group and not subtracted or used twice. The arithmetic difference of 38.00 is outside the minor tolerance and does not establish the allocation or current debt. Three further principals remain unknown (Nandy and two group-meal shares).

One new clarification asks how SBI_0023 applies to the ticket obligations. All six existing origin clarification IDs remain referenced; no duplicate requests were created for Nandy or group-meal uncertainty. There are no new prompts solely for a qualifying rounding difference. Original candidate questions about the four financially uncertain annotations are still in the origin document, without invented answers or obligations.

Rules: prefer applicable confirmed 3B reimbursement links; otherwise require positive raw repayment language, named counterparty, chronology and an explicit origin ID/reference (or an unambiguous loan/debt context). The immediate-return rule above is separately constrained. Multiple payments may settle one origin; allocation across origins requires explicit source amounts, never an assumed ordering or equal split. Bounded supported syntax is `<origin transaction ID>: <amount>` or `= <amount>` alongside repayment and counterparty text. Malformed, duplicate or excessive splits remain unresolved. Shared receipt capacity is enforced globally. No new financial events are generated.

All arithmetic uses Decimal. Original principal, actual verified repayment, signed adjustment, arithmetic residual and confirmed outstanding amount are separate. Exact full verified repayment closes an obligation. The INR 5 inclusive adjustment applies only to the final residual with strong final-settlement evidence, never per installment. Unexplained excesses are not adjusted and their financial treatment remains flagged; documented minor excess rounding can use a signed negative adjustment. Cross-bank same-day payments do not acquire fabricated intraday order. Conflicting dated balance statements prevent closure.

OPEN/PARTIAL require explicit dated source confirmation of no repayments or an outstanding balance agreeing with the verified ledger, through at least the supplied bank-data horizon. Without it, a positive arithmetic residual is UNASSESSED, not confirmed debt. Generic assertions attached to an origin with several counterparties cannot establish each person's balance. This deliberately conservative rule avoids assuming missing cash/off-bank history is complete. Status dates describe the supplied evidence, not a live account balance.

Classification effects reference the existing bank movement and only the allocated repayment amount; they do not add cashflows. They preserve original effective labels, exclude supported repayment principal from income/expense and leave any unallocated portion or unexplained excess for review. In particular, SBI_0026 receives settlement-derived repayment treatment while its original 3C.1 unknown classification remains intact. Consumers must join this overlay with the earlier effective view rather than summing settlement records as new money.

Verification: **125 tests passed (24 new)**, covering exact/partial/multiple/cross-account repayments, payables, explicit multi-origin allocations, final-residual tolerance including 5.00 and 5.01 boundaries, unexplained/documented overpayments, missing coverage, unknown principal, conflicting statements, competing origins/credits, excluded flows, stale inputs, tampering and source preservation. Real-data checks verified eight unique views, five unique allocated bank receipts, byte-identical reruns and reversed-input-order results. All **17** protected input and upstream-output file hashes remained unchanged. No new dependency, OpenAI call, PostgreSQL, dashboard, frontend or assistant. **Stop before Phase 3C.4.**

## Approved rule for Phase 3C.3: minor settlement differences

Implemented in the Phase 3C.3 settlement view; original Phase 3C.2 outputs remain unchanged and UNASSESSED.

- After strong evidence links actual repayment(s) to the same obligation and supports full settlement, permit a final residual difference of up to INR 5.00 inclusive as a rounding/minor settlement adjustment. Amount similarity alone never establishes a match or full settlement.
- Preserve all original transaction amounts and references. Record actual repayment totals and the signed difference/adjustment separately, with supporting evidence; do not rewrite principal or pretend the adjusted difference was repaid.
- Apply tolerance once to the final obligation residual after all supported partial repayments, never separately to each installment. Mark settled only when the full-settlement evidence supports that conclusion.
- Do not request clarification solely for a qualifying minor difference. Do not apply tolerance to unexplained overpayments, a genuinely larger remaining debt, or uncertainty about the original principal/relationship itself.
- Phase 3C.3 tests must cover the inclusive INR 5 boundary, larger residuals, unrelated similar amounts, multiple partial repayments without cumulative per-payment tolerances, unexplained overpayments, genuine remaining debt, and preservation of original facts and actual repaid amounts.

## Phase 3C.2 complete: obligation origins, settlement UNASSESSED

Added `src/obligations.py` and `tests/test_obligations.py`. Generated and validated private `output/obligations/obligations.json`; updated README. Reads both 3A documents, 3B relationships, 3C.1 effective classification and the coverage audit. Validates the effective view against a deterministic rebuild and rejects stale audit/source snapshots. All prior financial records are unchanged; historical sections below describe earlier slices.

**Eight confirmed obligation origins, all receivables: one temporary advance and seven reimbursement claims across five expenses. No supported payable origins in the current data.** Known original receivable principal is **INR 6,603.00**; known payable principal is **INR 0.00**. Three additional receivable principals are unknown and excluded from that sum. These are historical origin amounts, NOT a currently outstanding balance. Every origin has settlement status `UNASSESSED`; none is asserted `OPEN`, paid or closed.

| Origin | Counterparty | Type | Original principal (INR) |
| --- | --- | --- | ---: |
| SBI_0025 | Dad | Temporary advance | 3,000.00 |
| KOTAK_0014 | Jeevin | Shared ice-cream reimbursement | 40.00 |
| KOTAK_0014 | Manasa | Shared ice-cream reimbursement | 25.00 |
| KOTAK_0040 | parents (group, not individually allocated) | Train-ticket reimbursement | 2,364.00 |
| KOTAK_0058 | parents (group, not individually allocated) | Train-ticket reimbursement | 1,174.00 |
| KOTAK_0011 | Nandy | Electricity reimbursement | Unknown: note 131 versus follow-up 131.50 |
| KOTAK_0032 | Manasa | Group-meal reimbursement | Unknown: reported 180 may be a receipt rather than full original share |
| KOTAK_0032 | Deena | Group-meal reimbursement | Unknown: reported 100 does not establish the original share |

The group meal was 530 with a stated personal share of one third. The explicitly mentioned 180/100 amounts and AI components are preserved as evidence; neither those payments nor an equal split are promoted to confirmed original principal. No residual, partial repayment, or settlement allocation is calculated. The parents' full ticket amounts are supported by explicit whole-beneficiary purchases and repayment promises, not by their incorrect AI loan categories alone.

Six unanswered clarification candidates: one for each of KOTAK_0026, KOTAK_0027, KOTAK_0060 and SBI_0009 (obligation existence/beneficiary/funding terms); one for KOTAK_0011 (conflicting amount); and one for both KOTAK_0032 shares (original share amounts). The four financially uncertain annotations do not become obligations. Candidate records retain original note/follow-up, transaction/event/relationship IDs, proposed question, requested answer fields, stable question ID and null answer. Future revisions require respondent/time/raw-answer/prior-interpretation provenance; no assistant or answer-application workflow is implemented.

Existing cross-account reimbursement and possible-return links remain referenced as context with `NOT_ASSESSED` settlement effect. Receipt transactions never create duplicate origins. Planned account transfers, self-transfers, ATM withdrawals, cash exchanges, arbitrary unknowns and salary do not create obligations. The 60 missing-workbook unknowns and four blank-note unknowns remain untouched, without new clarification prompts.

Origin IDs depend on originating transaction, receivable/payable direction and source-level counterparty, not amount/status or processing order. Same-origin repeated components are grouped, conflicting principals remain null, and multiple shares cannot exceed the gross origin. No global person resolution is claimed; counterparty identity corrections will need an explicit future revision link. The schema references existing bank/event/relationship records and retains evidence hashes, source text, confidence, amount basis, and currency basis (owned INR statement scope). Explicit borrowing supports payables; source-backed `I owe <party>` semantic components support other payables. AI-only payable labels or own-account transfer components do not.

Verification: **101 tests passed (20 new)**. Real data: eight unique origins, six questions, all settlement statuses UNASSESSED; repeated runs produced byte-identical JSON and reversed 3A document order produced the same derived result. All **16** protected source/audit/upstream-output file hashes remained unchanged. The original 154 bank records, 85 meanings, effective classifications, and 27 relationship records were preserved. Tests cover both directions, source/AI disagreement, cross-bank context, known/unknown shares, conflicting amounts, duplicates, bystander exclusion, missing/blank notes, cash/transfer exclusions, candidate returns, stale inputs and output tampering.

Limitations: bounded deterministic language rules, not exhaustive interpretation; uncertain principal cannot contribute to totals. No new dependency, OpenAI call, repayment matching, settled/outstanding calculation, partial allocation, closure, database, or UI. **Stop before 3C.3 for review.**

## Phase 3C.1A complete: unknown classification coverage audit

Added `src/audit.py`, five synthetic regressions in `tests/test_audit.py`, and the private transaction-level `output/audit/coverage_audit.json`. No pipeline classification rules or existing financial outputs changed: the investigation found no skipped transaction notes, lost events, or deterministic classification defect to repair. All earlier status sections below remain historical.

The audit reparses the original Kotak CSV and SBI unencrypted XLSX (there is no SBI CSV), compares every bank fact against bank and 3A JSON, independently scans original notes workbook rows including hidden rows and rows after footers, and compares extracted text with saved Stage 2 source rows. Date/amount/direction/narration independently associate all 85 workbook rows one-to-one with bank transactions, agreeing with 3A. A complete validated 3C.1 rebuild equals the saved view, including original semantic projections and 3B relationship provenance.

| Source | Bank transactions / dates | Notes rows / dates | Populated meaning cells | Events / matches |
| --- | --- | --- | ---: | --- |
| Kotak | 103; 2026-08-07 through 2026-09-15 | 60; 2026-08-07 through 2026-08-25 | 56 | 60 / 60 |
| SBI | 51; 2026-08-01 through 2026-09-13 | 25; 2026-08-01 through 2026-08-26 | 23 | 25 / 25 |

Original 68 unknown treatments: **60 missing workbook rows, four blank notes, four populated notes with uncertain financial terms**. The audit lists all 154 transactions, original bank row locations, notes sheet/row/cell and original meaning, event/match presence, classification rule, reason and responsible stage, plus the exact original 68 IDs. No annotations were found skipped outside transaction tables; SBI cells D32/D33 are summary column heading/value (`Cr Count`, `5`), retained in the scan inventory as nontransaction cells, not user meanings.

The four unknowns with populated notes are KOTAK_0026 (50.00) and KOTAK_0027 (40.00), payments at Akash's request; KOTAK_0060 (10.00), payment for someone else's item; and SBI_0009 (10000.00), money from Dad for flight tickets. Their purpose/context is retained. Personal expense versus recoverable payment, and gift versus loan/funding terms, are not established. Blank-note unknowns are KOTAK_0021, KOTAK_0024, KOTAK_0029 and SBI_0018. Two other blank-note transactions remain correctly classified as explicit bank fees.

Overlap is measured, not assumed: 60 are both unknown and unmatched; eight unknowns have matched events; nine unmatched transactions have supported financial treatment (four self-transfer legs and five ATM withdrawals). The remaining 77 have matched semantics and assigned treatment. All 85 meanings and all 154 bank transactions are preserved.

**Fixed: 0; remaining unknown: 68.** Stage 2 covers 100% of the supplied 85 workbook rows, but those workbooks cover only 85/154 bank transactions. It is source-workbook coverage that is incomplete (43 Kotak and 26 SBI rows absent), not API/extraction coverage. Additional notes for the later statement periods are needed to improve semantic coverage. The four uncertain annotated cases need factual clarification of financial terms; another AI pass cannot establish those facts. No API request is proposed on current evidence.

Verification: full local suite **81 tests passed**; two audit runs produced identical bytes; all 15 protected input and upstream-output SHA-256 hashes were unchanged. Existing financial outputs and relationships were not regenerated or modified. No new dependencies, OpenAI calls, obligations, settlement engine, or database work. Stop here before 3C.2.

## Phase 3C.1 complete ? current status

Added `src/classification.py` and `tests/test_classification.py`; updated README. Generated and fully validated `output/classification/effective_transactions.json`. This is a deterministic effective view combining authoritative bank facts, preserved 3A semantics and evidence-supported 3B treatments. No source files or earlier-stage implementation were changed. Earlier statuses below describe historical slices.

Coverage: exactly 154 unique transactions, 85 unique linked semantic events, 69 without events. All 70 semantic meanings among the 130 3B-unclassified transactions remain present. The original event ID/hash links full semantic content without duplicating entire event documents. Core merchant/purpose/category/description/confidence fields are preserved verbatim and separate from effective treatment.

| Effective treatment | Count |
| --- | ---: |
| Expense | 62 |
| Self-transfer legs | 6 |
| Cash conversion | 6 |
| Reimbursement received | 5 |
| Recoverable/shared expense, allocation deferred | 5 |
| Loan advance | 1 |
| Salary income | 1 |
| Unknown | 68 |

Rules: supported 3B treatment overrides mistaken AI labels; unresolved funding/beneficiary terms block forced personal-expense classification; explicit bank fees can support expenses without notes; otherwise ordinary purchase/service semantics with a note, adequate confidence and no semantic ambiguity support expenses. Unknown bank movements stay unknown. Plans/candidate links are recorded but never treated as executed. Source category is not an accounting instruction. One treatment per bank transaction prevents counting relationship records as additional flows.

Investigated initial unknown classifications: the application-fee row carries a software-purchase AI label but still represents an ordinary expense; supported purchase types now include software purchase. The SBI ATM-card maintenance-charge narration independently supports an expense despite a blank note and generic AI label. No source semantics were rewritten to make either decision.

Uncertainty: 68 unknown treatments (60 without events, eight with insufficient meaning/terms), five deferred allocations, eight transactions participating in or candidates for unresolved relationships, six with semantic uncertainty. Groups overlap. Unknown rows with semantic events: KOTAK_0021, KOTAK_0024, KOTAK_0026, KOTAK_0027, KOTAK_0029, KOTAK_0060, SBI_0009, SBI_0018. The 69 unmatched-event transactions include nine with supported 3B treatment, explaining why unmatched-event and unknown-treatment counts differ.

Verification: full suite 76 tests passed (12 new focused tests). Real-data checks confirmed SBI_0001 printouts remain an expense with purpose; both 20,000 transfer legs are non-income/non-expense; SBI reimbursements are not new income; ATM/cash conversions remain asset movements; SBI_0025 is an advance while its original loan_repayment label remains intact. Source/event coverage, counting flags, serialized result validation, stale-3B fingerprints and unsupported special treatments are checked. All original input/bank/event/3A/3B SHA-256 hashes remained unchanged. Rerunning with reversed input-file order produced identical output bytes.

No OpenAI calls, ML training, obligations, repayment matching, partial settlements, PostgreSQL, dashboard or assistant integration. Stop after 3C.1 and await review before 3C.2.

## Phase 3B complete ? current status

Added `src/relationships.py` and `tests/test_relationships.py`; documented the rules and limitations in README. Generated and validated `output/relationships/relationships.json` from both 3A documents. No source bank, event or reconciled JSON files were modified; original input and output-source SHA-256 checks passed. All earlier stop-before-3B notes below are historical.

27 relationship records across all 154 bank transactions: 12 confirmed, 6 identified, 2 planned, 2 source-asserted and 5 unresolved. Confirmed: 3 self-transfer pairs (including the monthly spending allocation), 4 reimbursement links and 5 ATM cash conversions. Source-asserted: one cash exchange and one purpose-specific funding receipt. Identified: one temporary advance and five recoverable/shared expense claims. Explicitly planned: two instructions to move received reimbursements between accounts; no execution inferred.

Unresolved: SBI_0023 reimbursement allocation to the two parent-ticket expenses; SBI_0025 possible return candidate SBI_0026; payments on behalf of others KOTAK_0026, KOTAK_0027 and KOTAK_0060. No amounts were forced to balance, no candidate was treated as repayment, and no partial-settlement or closure state was created. Expected-reimbursement records are original claim evidence, not an assertion that they remain unpaid.

Derived financial classifications: self_transfer 6; cash_conversion 6; reimbursement_received 5; recoverable_or_shared_expense 5; loan_advance 1; salary_income 1; unclassified 130. Unknown income/expense flags are null, not zero. No full spending or income total is claimed. Bank balances and original event meanings remain in the unchanged upstream JSON. In particular the salary allocation is not new income, and the temporary advance is not accepted as a loan repayment merely because AI labeled it that way.

The relationships output references transaction IDs instead of duplicating transactions. Each transaction has exactly one financial treatment. Linked relationship amounts are evidence only, not extra cashflows. Cash conversion records are movements into cash, not cash balances or spending estimates. Input fingerprints, source-row/event IDs, original note evidence, derived classification, rule, confidence and status support auditability.

Verification: all 64 tests passed (16 new relationship tests). Both 3A documents are validated on load. Result structure and integrity validated before atomic save and after JSON serialization. Repeated real-data generation, including reversed input-file order, produced byte-identical output. All original input and bank/event/reconciled files remained byte-identical. No new dependencies, OpenAI calls, ML training, PostgreSQL, dashboard, assistant, or Phase 3C lifecycle work.

Limitations: bounded positive-evidence rules, not exhaustive semantic understanding. Shared UPI IDs are required for self-transfer confirmation; explicit raw notes must corroborate AI-proposed reimbursement shares. Receipt matching uses a 30-day window and conservative sequence checks. Most ordinary bank movements remain outside this slice's financial classification. ML would need labeled examples and evaluation and is not proposed as a reliable shortcut. Stop after 3B for review.

## Phase 3A final refinement ? 2026-10-22

Generic numeric-reference precision handling is complete in `src/reconcile.py` (output schema 3A.2). Accepts scientific notation and trailing-zero decimal rounding only when the authoritative numeric reference rounds exactly to the source precision, with at least three significant digits. Uses Decimal ROUND_HALF_UP, not floats. Alphanumeric identifiers, meaningful leading zeros, nonmatching rounding and excessively coarse references do not receive this exception.

The exception additionally requires exact date, amount, direction and whitespace-normalized narration, mutual candidate uniqueness and chronological consistency. Merchant/payee names supported by both narrations are recorded as supporting evidence; AI names alone never establish a match. Sequence alone never establishes identity. Conflicting duplicate candidates remain ambiguous. Both references and rounding quantum remain in matching evidence; the full original bank reference remains authoritative and unchanged. Precision discrepancies resolved this way do not trigger manual review. Existing semantic uncertainty flags remain independent.

KOTAK_EVENT_0047 now matches KOTAK_0047 without a review flag. Final results: Kotak 103 bank rows / 60 events / 60 matched / 43 unmatched bank / 0 ambiguous; SBI 51 bank rows / 25 events / 25 matched / 26 unmatched bank / 0 ambiguous. No unmatched or ambiguous events. Existing semantic review counts remain Kotak 4, SBI 2.

Regenerated and validated both reconciled outputs. All 48 tests passed, including rounded/scientific references, genuinely conflicting references, insufficient precision, leading-zero/alphanumeric protection, duplicate rounding collisions, sequence and primary-fact conflicts. Byte-identical reruns verified. SHA-256 checks confirm all original input files and all source bank/event JSON files remain unchanged. No API calls or Phase 3B work. This status supersedes the earlier unresolved reference-conflict notes below.

## Phase 3A completed ? 2026-10-21

Implemented deterministic sequence-aware bank-to-event matching in `src/reconcile.py`; added `tests/test_reconcile.py` and README documentation. No source bank/event JSON was modified (all four SHA-256 hashes verified before/after). No OpenAI calls, transfer linking, cash tracking, open-event management, PostgreSQL or other later-stage work.

Generated and validated `output/reconciled/kotak_reconciled.json` and `output/reconciled/sbi_reconciled.json`. Every bank row and every original event/source document is preserved. Provenance, candidates, evidence, local corrections and semantic review flags remain available. Categories are never inferred by reconciliation.

| Bank | Bank rows | Events | Matched | Unmatched bank | Ambiguous bank | Unmatched events | Ambiguous events | Matched meanings needing review |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Kotak | 103 | 60 | 59 | 43 | 1 | 0 | 1 | 4 |
| SBI | 51 | 25 | 25 | 26 | 0 | 0 | 0 | 2 |

One unresolved identity conflict: KOTAK_EVENT_0047 (source kotak:50) and KOTAK_0047 have the same date/amount/direction/narration but different explicit references. Kept as ambiguous with no attached meaning. It must not be silently forced into a match. The SBI narration wrap discrepancy is handled by ignoring whitespace only; original strings remain untouched.

Matching requires exact primary facts and strong source reference/narration evidence, then uses mutually unique chronology-consistent anchors. Identical duplicates and weak candidates remain ambiguous; date-only/amount-only guesses are not accepted. Sequence gaps are allowed; spreadsheet source IDs are not bank serials. Rules are documented in README.

Full current suite: 44 tests passed (11 new reconciliation tests). Real-data outputs passed validation before saving and after serialization. Repeating both runs produced byte-identical output files. Source hashes remained unchanged. Phase 3A is complete; review the unresolved reference conflict and matching policy before authorizing Phase 3B. Earlier status entries below are historical.

## Current final status

Kotak and SBI Financial Events are complete. Applied the three user-approved local Kotak corrections and fully validated all 60 Kotak events before atomic saving. Revalidated all 25 SBI events against their source rows; the SBI file is unchanged. Kotak has zero duplicate events, four ambiguity flags and three low-confidence events. The other 57 Kotak events are unchanged. Both files preserve original source rows. Kotak additionally records the three before/after corrections, reasons and original-response SHA-256 for provenance.

Per user instruction, removed the obsolete Kotak response, Kotak diagnostics and SBI batch response after successful final validation. `output/events/` now contains only `kotak_events.json` and `sbi_events.json`. No new API call or reconciliation work was performed. Earlier pending/blocked statements below are historical and superseded by this status.

## Latest update: uncertainty rules (2026-10-20)

Updated the event prompt to prefer generic/null meaning when evidence is insufficient; no forced small-transaction Miscellaneous classification. Below INR 50, missing notes and insufficient narration can yield null description/category/purpose. INR 5–100 uses contextual judgment; Miscellaneous generally indicates an attention-worthy unknown purpose around INR 100+. Known small expenses retain supported meaning. Python does not implement semantic amount thresholds.

FinancialEvent.description is now nullable. Description/category/subcategory/purpose/event_type reject empty strings in favor of null. Removed the unconditional nonempty-description business check. All source coverage/order/date/amount/direction safeguards remain intact. All 33 tests pass; existing SBI events also validate under the revised schema.

Retained Kotak response from the single authorized fresh request contains 60 events and completed successfully at the API level. Its original failure was empty descriptions on kotak:24, kotak:27, kotak:32. Usage: 5,824 input + 8,424 output tokens. Those three inputs have no meaning notes; returned auto-fare/grocery assumptions lack sufficient support.

Recommended local resolution, not yet committed: explicit reviewed overrides on those three IDs only. Set description/category/subcategory/purpose/event_type to null; remove auto-rickshaw payees; retain named people as payees and Blinkit as merchant; mark unknown purpose and low semantic confidence. Preserve all bank facts and other 57 events. Preserve raw response and record corrections separately with reasons/provenance. An in-memory dry run of these exact corrections passed validation for all 60 events against the matching source fingerprint. No corrected Kotak output or override file has been written, and no API request was made. This avoids reinterpreting already-returned events or embedding semantic rules in Python.

Updated: 2026-10-07.

Slice 0 and bank ingestion are complete. SBI Financial Events: 25 validated events saved. Kotak Financial Events: first live interpretation failed with an unspecified ValueError; no Kotak response was retained. Diagnostics are now fixed and locally tested; no new API request made during diagnosis.

## Inputs and outputs

- Kotak: `input/bank/kotak/AccountStatement_01-Aug-2026_16-Sep-2026.csv` -> `output/bank/kotak_transactions.json` (existing converter and output unchanged).
- SBI: `input/bank/sbi/sbi_unencrypted.xlsx` -> `output/bank/sbi_transactions.json`.
- Both outputs are pretty-formatted UTF-8 JSON arrays and remain ignored by Git.
- No PDFs or passwords were used. The earlier SBI encryption blocker is resolved by the supplied unencrypted workbook.

## Final results

| Bank | Transactions | Debit count | Debit total (INR) | Credit count | Credit total (INR) | Duplicates | Uninterpretable/skipped transactions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KOTAK | 103 | 94 | 26494.63 | 9 | 28724.00 | 0 | 0 |
| SBI | 51 | 34 | 103447.35 | 17 | 129053.79 | 0 | 0 |

Both saved outputs passed existing shared validation and have exactly the same field names and order. SBI counts and totals also match the source statement summary. Duplicate checks compare all normalized fields except transaction ID and retain/report matches.

## SBI source structure and mapping

The actual XLSX contains one worksheet, `sheet`, with six columns. Header row 18 is `Date`, `Details`, `Ref No/Cheque No`, `Debit`, `Credit`, `Balance`. Transactions occupy rows 19-69. Row 70 is blank; rows 71-73 contain the statement summary; row 74 is blank; rows 75-77 contain bank notices. Earlier rows contain account/statement metadata. These non-transaction rows are excluded.

- Account comes from the explicit Account Number metadata; identifiers remain text.
- Date strings are day/month/year and become ISO dates. Native Excel dates are also supported.
- Exactly one of Debit/Credit must be populated; its value supplies amount and direction. Amounts and balances use the existing Decimal-based two-decimal normalization.
- Details is retained verbatim, including leading spaces and embedded newlines.
- The explicit reference column is blank for all 51 transactions, so references are null. No reference is inferred from narration.
- Balances come directly from Balance; missing optional facts become null.
- IDs SBI_0001 through SBI_0051 follow worksheet transaction order, without sorting.
- Unrecognized transaction/footer rows, ambiguous amounts, formulas/error cells, unexpected columns/sheets, incomplete structure, or summary mismatches fail before output writing. Row errors include sheet and row number. Parsing is scoped to this observed layout; materially different exports require inspection.
- IDs are deterministic within a statement, not global across multiple statements.

## Changes and verification

Updated `src/sbi.py`, `tests/test_sbi.py`, `requirements.txt`, `README.md`, and this file. The sole direct dependency is `openpyxl==3.1.5`. Kotak conversion and shared schema/validation were not modified.

Ran `.venv\Scripts\python.exe -m src.sbi input\bank\sbi\sbi_unencrypted.xlsx`: success; 51 validated records, no duplicates.

Ran `.venv\Scripts\python.exe -m unittest discover -s tests -v`: all 19 tests passed (9 Kotak, 10 SBI). Synthetic tests cover parsing, original narration, optional fields, dates, amounts, directions, balances, deterministic IDs, unsafe rows, summary mismatch, unexpected sheets, duplicate retention, encryption rejection, UTF-8 JSON round-trip, and schema consistency between actual parser outputs. No real private source records are embedded in tests.

## Financial Events implementation

Added `src/notes.py`, `src/event_models.py`, `src/events.py`, and `tests/test_events.py`. Updated dependencies and README. Bank converters, bank models, bank validation, and bank outputs remain unchanged.

Actual notes inspected:

- `input/notes/kotak/kotak_aug_notes.xlsx`: sheet `kotak`, header row 3, 60 transaction rows (4-63), 4 without a meaning, 1 with a `missed` follow-up. One numeric reference is normalized to text. No transaction rows skipped.
- `input/notes/sbi/sbi_aug_notes.xlsx`: sheet `sbi`, header row 4, 25 transaction rows (5-29), 2 without a meaning, 2 with `missed` follow-ups. No transaction rows skipped.

Only useful row facts, narration, notes, references, value date and follow-ups are sent. Personal header metadata, balances, bank notices and summaries are omitted. Source note wording and whitespace are retained. The same reader/processor handles both layouts by column labels, not bank-specific semantic rules.

Semantic interpretation is exclusively through OpenAI Responses Structured Outputs, parsed and validated with Pydantic. The default model is `gpt-4.1-mini-2025-04-14`. Each current workbook fits one whole-file request; larger files use sequential batches of approximately 50. No concurrency, agents, tools, retries, recursion, intermediate prose or repeated interpretation passes. `store=False` is set.

Schema includes primary event type/date/amount/direction, currency, merchant/payee/payer, category/subcategory, description, purpose/context, entities and aliases, related components (shares/repayments/cash legs/planned actions with status), matching clues, confidence, ambiguities and other supported context. Unknown scalars are nullable. Every row must have exactly one primary event; related financial meaning is preserved in components. Source coverage/order and immutable dates/amounts/directions are validated before merge/write. Atomic saving protects existing outputs on failure.

Full suite: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 27 tests passed, including 8 new synthetic/mocked event tests. API tests are mocked and do not establish live API availability or semantic correctness.

Initial live run stopped locally because the key was empty. After the user supplied a key and requested another attempt, the Kotak whole-file request reached the SDK but failed with AuthenticationError. No successful interpretation was returned; SBI was not attempted with the same rejected credentials. No credentials were printed or changed. Both `output/events/kotak_events.json` and `output/events/sbi_events.json` remain pending. Actual event counts, usage, semantic ambiguity and final validation results are unavailable until live generation succeeds.

Required next action: replace OPENAI_API_KEY in the ignored local `.env` with a valid OpenAI API key; then run both documented commands sequentially, validate saved outputs, and record final counts/usage. Do not put the key in chat or tracked files.

Reconciliation, Streamlit, analytics, databases and PDF processing remain unimplemented.


## Live generation follow-up

The local API key is now available; its value was never printed. Executed one Kotak request (60 rows): response failed local ValueError validation and no final Kotak output was committed. The original generic error handler suppressed the specific validation reason and did not retain that response, so the exact failure cannot be diagnosed or repaired locally. No automatic retry was made.

Improved diagnostics to report safe validation messages and retain subsequent raw API responses under ignored output/events before local validation. This enables local inspection without repeating interpretation. Updated the affected mock test signature; all 27 tests passed again.

Executed one SBI request (25 rows): generated output/events/sbi_events.json. Re-read the saved JSON and validated all 25 events against the schema and original source-row coverage/order/date/amount/direction. Zero duplicate events; two events have ambiguity flags; zero low-confidence events reported by the model; two source rows have no meaning note. Usage: 3,314 input tokens and 3,315 output tokens. These are model interpretations, not verified reconciliation results.

Kotak retry requires user authorization because the user explicitly prohibited repeated interpretation passes. SBI is complete; Kotak output remains pending. No reconciliation work performed.

## Kotak failure diagnosis (no API requests)

The only retained failure evidence is the terminal message `Event generation failed (ValueError); no new output committed.` No Kotak raw response, request ID, token usage, response status, event count or detailed validation exception is available in project artifacts. Therefore the precise original failure and whether all 60 events were returned cannot be recovered. Earlier attribution to a particular validation step would be unsupported. Any earlier authentication/key-blocker text is historical, not the current state: SBI subsequently succeeded.

Local preflight confirms 60 readable Kotak source rows, 60 unique source IDs and one batch (~15 KB serialized rows). Missing meanings are allowed. Plain ValueError paths include response status/no parsed output, coverage/order, changed date/amount/direction, and empty description. Schema failures normally have the distinct ValidationError type. Duplicate semantic events are reported, not rejected. There is no retained evidence of output truncation or batch-size causation.

Confirmed code issue: response auditing was after SDK parsing, so parsing errors could still lose the response; original errors were generic and coverage errors conflated distinct causes. Fixed by requesting strict JSON Schema with responses.create, saving responses with API field aliases before local JSON/Pydantic/business validation, distinguishing refusals/incomplete status and reason/malformed JSON/schema failures, aggregating coverage and changed-fact diagnostics, saving diagnostic sidecars, and using unique response filenames to prevent overwrite. Financial Event fields, prompt, fact-equality safeguards and batch size are unchanged.

Validation: 30 local tests pass, including new malformed-JSON/schema archival, separate refusal/truncation, and aggregated coverage/source-fact error tests. Offline replay of retained SBI response through the new handling path validates all 25 events, using a mocked client with no network. Historical SDK serialization used schema_ rather than API schema; the in-memory replay maps that alias only. New archives use by_alias=True. Existing event/bank output files were not changed.

Recommendation: retain the 60-row batch and schema, with corrected capture/diagnostics. One fresh request is technically ready to run when authorized, with no automatic retry and no guarantee of semantic/validation success. If it fails, raw output and exact diagnostics can be inspected locally. No additional interpretation request was made during diagnosis.
