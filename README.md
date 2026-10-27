# Finlyst

A deliberately simple local Python rebuild. Slice 0 and both bank converters are complete.

Phase 3R.2C adds a persistent durable-ID registry without changing existing financial
JSON or stage commands. See [DURABLE_IDS.md](DURABLE_IDS.md) for the legacy-ID audit,
overlap/continuity rules, Python integration and PostgreSQL migration design. Keep the
private `output/identities/identity_registry.json` across imports and back it up with
the source artifacts; Git deliberately excludes it.

## Unified pipeline CLI

From the project root, with the project virtual environment active:

```powershell
python -m src.cli --help
python -m src.cli run --offline
python -m src.cli run --from reconcile --to integrity --offline
python -m src.cli reconcile --bank all
python -m src.cli validate --all
```

`run` defaults to offline operation even without `--offline`. It executes sequentially:

```text
bank -> events (reuse saved JSON) -> reconcile -> relationships -> classification
     -> audit (notes coverage) -> obligations -> settlements -> identities -> integrity
```

Each stage name is also an individual command. `bank`, `events` and `reconcile` accept
`--bank kotak`, `--bank sbi` or `--bank all` (the default). Cross-account stages consume
both banks. `--from` and `--to` select an inclusive contiguous range; earlier outputs
must already exist. A reversed range is rejected before execution.

Select a different project directory with `--root`, before or after the subcommand:

```powershell
python -m src.cli --root D:\finlyst validate --all
python -m src.cli reconcile --root D:\finlyst --bank kotak
```

Paths under that root retain the existing `input/` and `output/` structure. Bank input
selection requires one Kotak CSV and one `sbi_unencrypted.xlsx`; notes require one XLSX
per bank. PDFs are not processed. Missing or ambiguous sources fail with a path-specific
error instead of choosing arbitrarily.

The events stage validates saved events against their normalized bank facts and the
current notes workbook. Existing events are never automatically regenerated. Missing
events fail offline. Explicit API permission is available only on `run` and `events`:

```powershell
python -m src.cli events --bank kotak --allow-api
```

This authorizes generation **only if that bank's event output is missing**. It uses the
existing event processor, batching, model and retained diagnostics, with zero automatic
network retries. It loads `OPENAI_API_KEY` from the environment or the selected root's
ignored `.env` only on that API path. Existing valid outputs are reused even with
`--allow-api`; stale or invalid saved events require review and are never silently
reinterpreted. `--allow-api` and `--offline` are mutually exclusive.

`validate --all` is strictly read-only: it validates saved schemas, replays the existing
integrity checks against original sources, checks the saved integrity report and checks
identity mappings against the current snapshot. It neither saves reports nor repairs
missing/stale mappings. All stage outputs, including the integrity report and registry,
must already exist. `--help` loads no stage runners and performs no project reads/writes.
Use Python's `-B` option if you also want to suppress interpreter bytecode caches.

Progress names each stage. Exit codes are 0 for success, 1 for execution/validation
failure and 2 for invalid arguments. Execution stops at the first failed stage; earlier
completed stages are not rolled back. An integrity-stage failure may save its diagnostic
report, while `validate` never writes. The CLI retains the integrity runner's existing
154-transaction/85-event dataset-specific assertions; it does not silently loosen those
checks for future datasets. No financial rules, IDs, classifications or evidence change.

## Running the synthetic tests

From the project root, install the pinned dependencies in a Python virtual environment
with `python -m pip install -r requirements.txt`, then run:

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -v
python -m unittest tests.test_classification -v
```

Both discovery commands run the full suite; the last command runs one module independently.
Tests generate synthetic CSV/XLSX/JSON files in temporary directories under `tests/`
and clean them up afterward. No existing `tests/fixtures/`, private `input/` or
`output/`, `.env`, or API key is required. API interactions are mocked. Shared
builders live in `tests/support.py`; test modules do not import each other.

## Strict financial output contracts

`src/output_models.py` defines reusable Pydantic contracts; `src/schema_validation.py`
validates loaded inputs and generated outputs without replacing the original dictionaries.
Use `validate_schema(Model, document, stage)` for in-memory values and
`parse_document(content, Model, path)` for JSON text/bytes. Errors identify the stage or
path and failing field. Matching, financial calculations and existing invariant checks
remain separate from structural validation.

| Document | Model | Accepted version |
| --- | --- | --- |
| Saved events | `EventDocument` | `1.0` |
| Reconciliation | `ReconciliationOutput` | `3A.2` |
| Relationships | `RelationshipsOutput` | `3B.1` |
| Effective classification | `ClassificationOutput` | `3C.1.1` |
| Coverage audit | `CoverageOutput` | `3C.1A.1` |
| Obligation origins | `ObligationsOutput` | `3C.2.1` |
| Settlements | `SettlementsOutput` | `3C.3.2` |

Normalized bank files retain their existing unversioned array format, validated by
`BankTransaction`/`BankTransactions`. `ObligationsFileOutput` and
`SettlementsFileOutput` additionally require file-verification metadata; pure builders
do not manufacture that metadata. Missing/unsupported document versions, malformed
records, unrecognized controlled values and extra fields are rejected before saving.
Unknown/null meanings, unresolved relationships, unknown principals, UNASSESSED and
signed settlement adjustments remain valid where their contracts allow them.

Validation does not serialize models back into financial files or insert default keys.
Existing output versions, IDs, hashes, approved correction history and formatting are
preserved. Schema validity complements financial evidence checks; it does not prove
that a semantic interpretation is correct. New versions require an explicit compatible
contract or migration, never an automatic reinterpretation. Run the focused regressions
with `python -m unittest tests.test_output_schemas -v`.

Planned direction:

```text
Bank files → Bank JSON
Notes → Financial Event JSON
Bank JSON + Event JSON → Reconciliation
Reconciled JSON → Streamlit inspection dashboard
```

- `input/bank/kotak/`: private, non-password-protected Kotak PDF and CSV files placed manually.
- `input/bank/sbi/`: private, password-protected SBI PDF and Excel `.xlsx` files placed manually.
- `input/notes/kotak/` and `input/notes/sbi/`: source notes and user meanings for each bank.
- `output/bank/`: future normalized bank transaction JSON.
- `output/events/`: future Financial Event/User Meaning JSON.
- `output/reconciled/`: future mapped/reconciled JSON.
- `src/`: future Python implementation.
- `tests/`: future automated tests; `tests/fixtures/` is exclusively for sanitized or synthetic fixtures.

The entire `input/` and `output/` directories are ignored by Git. Never commit real bank statements, private bank CSV/XLSX files, passwords, API keys, or generated private financial JSON. Never put the SBI PDF password anywhere in this repository.

Run the Kotak converter from the project root (using the Python virtual environment):

```powershell
.venv\Scripts\python.exe -m src.kotak "input\bank\kotak\AccountStatement_01-Aug-2026_16-Sep-2026.csv"
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The converter writes `output/bank/kotak_transactions.json` and prints validation, duplicate IDs, and debit/credit counts and totals. Duplicate records are retained and reported. Unrecognized transaction/footer rows cause an error before output is written. IDs follow source order within each statement. Narration is preserved without interpretation.

Run SBI ingestion with `.venv\Scripts\python.exe -m src.sbi input\bank\sbi\sbi_unencrypted.xlsx`; it writes `output/bank/sbi_transactions.json` using the same schema and verifies counts/totals against the statement summary. Install dependencies with `.venv\Scripts\python.exe -m pip install -r requirements.txt`.

## Financial Events

The shared processor reads each notes workbook once, normalizes source facts, batches rows, calls OpenAI for semantic interpretation, validates Structured Outputs, merges results, and atomically saves JSON. Python does not infer categories, parties, purpose, or financial meaning.

Set `OPENAI_API_KEY` in the ignored local `.env` file or environment. These commands send the normalized notes to OpenAI; no bank JSON or PDFs are sent:

```powershell
.venv\Scripts\python.exe -m src.events input\notes\kotak\kotak_aug_notes.xlsx --bank KOTAK
.venv\Scripts\python.exe -m src.events input\notes\sbi\sbi_aug_notes.xlsx --bank SBI
```

Outputs are `output/events/kotak_events.json` and `output/events/sbi_events.json`. Each document contains provenance, local source rows, a validation/token report, and an `events` array. `source_id` links events to sheet/row; event IDs are deterministic within a file, not globally unique across months. Use `--output` to keep different months separately.

The model interprets one primary event per source row, with related shares, cash legs, receivables, obligations, and intended actions in `components`. Fields cover type, date, amount, direction, currency, merchant/payee/payer, categories, description, purpose, entities/aliases, matching clues, confidence, ambiguity and additional context. Unknown scalar values are null and unused lists are empty. Raw notes and follow-ups are preserved locally for audit. Model interpretations remain unverified assertions; structural validation does not prove semantic correctness.

Default model: `gpt-4.1-mini-2025-04-14`, using `responses.create` with strict JSON Schema [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), followed by local Pydantic validation. Raw responses are saved under unique filenames in ignored `output/events/` before JSON/schema/business validation. Failures retain separate diagnostic files, including expected coverage and source fingerprints. `store=False`; automatic retries are disabled. There are no tools, agents, parallel requests, recursive processing, or second interpretation passes.

Files up to 65 rows and 80 KB of compact input use one request. Larger files use sequential batches of 50 by default (configurable with `--batch-size`, capped at 65 to reserve output capacity). Oversized payloads fail before API calls. The current files contain 60 Kotak rows and 25 SBI rows, so each needs one request. Batches share no hidden conversation state: interpretation context is the current batch. `--model` may select another compatible model; the fixed budgets must fit that model.

The reader recognizes the inspected annotated statement columns by label, including `Meaning`/`Meaning..` and `missed`; it does not depend on a bank name or month. Missing notes are retained for uncertain interpretation. Formulas, invalid dates/amounts and ambiguous debit/credit fields are rejected. New workbook layouts require reader support rather than semantic guesses. Missing/duplicate/reordered source IDs or altered source dates/amounts/directions invalidate API output. Refusal, truncation, or validation failure leaves any existing output unchanged.

Phase 3A bank-to-event reconciliation is implemented. Phase 3B relationships are implemented as described below. Open-event lifecycle management, Streamlit, databases and PDF processing are not implemented.

Unknown event meaning is valid: `description`, category/subcategory, purpose and event type may be null; empty strings are rejected in those fields. Small unknown INR transactions (especially below 50) should remain generic rather than defaulting to Miscellaneous. Miscellaneous is reserved for materially attention-worthy unknown purposes, generally INR 100+. INR 5–100 requires contextual judgment, not Python amount-based classification. Clear merchant/payee identities and supported small expenses remain useful even when purpose is unknown. These are semantic instructions for the model; local validation checks structure and source facts, not the truth of inferred categories.


## Phase 3A: bank-to-event reconciliation

Run `.venv\Scripts\python.exe -m src.reconcile` from the project root (or add `--bank kotak` / `--bank sbi`). No API calls are made. Inputs are the existing bank/event JSONs; outputs are `output/reconciled/kotak_reconciled.json` and `output/reconciled/sbi_reconciled.json`.

Every bank record is preserved verbatim in `transactions[].bank_transaction`, in original order. A matched event is attached separately and unchanged. Category is copied only from that event; unmatched/ambiguous rows have null event/category. Null meanings and small-amount uncertainty remain intact. Matching confidence is separate from semantic confidence: `meaning_needs_review` preserves event ambiguity/low confidence even when identity matches securely.

Source IDs join events to their source notes; they are never treated as bank row numbers. Exact date, amount and direction are mandatory. An exact source reference or narration equality (ignoring whitespace only, including export line wraps) supplies identity evidence. Conflicting nonempty references block automatic matching except for demonstrable numeric precision loss: scientific notation or trailing-zero decimal rounding must equal the authoritative reference rounded at the source precision (at least three significant digits). That exception also requires narration agreement, exact primary facts, mutual uniqueness and chronological consistency. Alphanumeric and leading-zero identifiers are not reinterpreted. Both values and rounding evidence are retained; the original bank reference remains authoritative. Merchant/payee names supported by both narrations are recorded as additional evidence, never used alone to establish identity. AI matching clues, category and purpose are not identity evidence.

Matching uses chronological date order with original source order within each day. Mutually unique strong candidates become sequence anchors; remaining candidates must fall between those anchors. Source gaps are allowed. Identical duplicates, crossed same-day order, reference conflicts and date/amount-only candidates remain ambiguous. There is no fuzzy/date-tolerance fallback, greedy first-match selection or cross-account matching. This deliberately trades recall for safe attachment on different layouts or orderings.

Outputs preserve the entire event source document (including source rows, corrections and provenance), canonical input SHA-256 fingerprints, candidate evidence and decisions for every event. Ambiguous bank counts are distinct from unmatched-bank counts; their sum plus matched equals all bank rows. Ambiguous/unmatched events are retained in the source document and decisions rather than discarded. Original bytes are not rewritten. Outputs contain no run timestamp, so identical inputs produce byte-identical reruns.

Before atomic saving, input schema/fingerprints/source coverage and event facts are checked, then the complete result is deterministically rebuilt and compared after JSON serialization. Tests cover gaps, duplicate amounts, indistinguishable duplicates, sequence anchors/conflicts, references, narration wrapping, source tampering, unchanged inputs and idempotency.

Current results: Kotak 60 matched / 43 unmatched bank / 0 ambiguous; SBI 25 matched / 26 unmatched bank / 0 ambiguous. All 85 events are matched. The previously conflicting Kotak pair is resolved by the generic precision rule, without a manual-review flag. Existing semantic uncertainty is preserved separately. Stop here before Phase 3B.


## Phase 3B: financial relationships

Run `.venv\Scripts\python.exe -m src.relationships`. This reads the two validated 3A reconciled documents and writes only `output/relationships/relationships.json`. No new dependencies, model training or API calls are used. Inputs and their interpretations remain untouched.

The document contains relationship records, one derived financial treatment per bank transaction, input fingerprints and counts. Relationship records reference transaction IDs, event IDs and source rows; they do not duplicate bank transactions or overwrite bank balances. Source notes/follow-ups are retained as evidence. Relationships are descriptive records, **not additional cashflows**. Use each transaction treatment once; never sum amounts across relationships. `income`/`expense` null means unknown or allocation deferred, not zero. The existing 3A category is still available in its unchanged source file; this layer does not relabel all ordinary expenses.

Rules deliberately require positive evidence:

- Self-transfer: equal amount and date, opposite bank directions between owned-account documents, identical interbank UPI ID parsed from bank narration, and unique pairing. Both sides are excluded from income and expense; source purpose remains attached. Name/amount similarity alone is insufficient. Current files are treated as owned accounts by product scope; third-party statements must not be supplied as owned-account inputs.
- Reimbursement receipt: an actual credit and an explicit source repayment note. Expense links additionally require a unique earlier expense with an AI-proposed share whose party and amount are independently present in raw notes. Date gap is at most 30 days, same-bank same-day source order must agree, and cross-bank intraday order is not asserted. Competing credits for one share remain unresolved. No allocation, residual balance or closure is computed.
- Expected reimbursement: raw notes corroborate a receivable/shared payment. The gross debit is not automatically a personal expense. An expected reimbursement record remains historical evidence even if a separate receipt link exists; it does not mean an amount is still outstanding.
- Loan/advance: explicit source language and actual bank direction override mistaken AI categories. A later opposite-direction movement with equal amount and exact bank counterparty key is a possible return only; 3B does not settle it. Purchases for others with expected reimbursement remain recoverable expenses, not automatically loans.
- Cash: explicit ATM withdrawals are bank-confirmed cash movements; an explicit note saying cash was received in exchange for a matching UPI debit is source-asserted. Neither is an expense. These records describe additions to cash, **not current cash holdings**; spending, opening cash and settlements are absent.
- Plans: only explicit raw future-transfer instructions create planned records. AI components alone are insufficient, and no completed transfer is invented. Purpose-specific funding without clear terms stays source-asserted; it is not automatically a gift, loan or earned income.

These are bounded deterministic evidence rules, not general natural-language comprehension. Notes with different wording, bank identifiers other than the supported UPI layouts, missing support, or ambiguous allocations can remain unresolved/unclassified. No ML model is warranted without labeled examples and evaluation; no claim of exhaustive relationship discovery is made. Exact bank evidence and explicit notes are preferred to speculative predictions.

Current result: 27 relationships across 154 transactions: 12 confirmed (3 self-transfers, 4 reimbursement links, 5 ATM cash conversions), 6 identified intentions/claims, 2 plans, 2 source-asserted relationships and 5 unresolved records. The latter cover an unallocated train-ticket reimbursement, a possible loan return, and three payments on behalf of others with unspecified terms.

Financial treatments: 6 self-transfer legs, 6 cash conversions, 5 reimbursement receipts, 5 recoverable/shared payments, 1 loan advance, 1 explicit salary receipt and 130 unclassified movements. No complete income/expense total can be inferred from this partial classification.

Tests cover cross-bank and nonadjacent links, duplicate candidates, AI mistakes and unsupported components, planned vs executed actions, advances vs repayments, gift/loan uncertainty, cash evidence, double-counting protections, input immutability and deterministic reruns. Phase 3C outstanding balances, partial settlements and closure remain unimplemented, as do PostgreSQL and the dashboard.


## Phase 3C.1: effective financial classification

Coverage audit (3C.1A): run `.venv\Scripts\python.exe -m src.audit` to write `output/audit/coverage_audit.json`. It compares original bank sources, independently inventories notes workbook cells, checks every saved event/3A association, and validates a deterministic classification rebuild without rewriting financial outputs. The private report contains all 154 transactions, original source locations and notes, pipeline coverage, unknown reasons, the baseline unknown IDs, and protected-file hashes. It deliberately fails on inconsistent source snapshots or ambiguous source association rather than silently claiming coverage. It selects one CSV in the Kotak bank folder, `sbi_unencrypted.*` in SBI, and one notes XLSX per bank; multiple candidates require source selection to be reviewed.

Current audit: all 85 supplied notes rows became matched events; none were lost. The notes periods end August 25 (Kotak) and August 26 (SBI), while bank data extends into September. Of 68 unknown treatments, 60 lack workbook rows, four have blank notes, and four have uncertain beneficiary/funding terms. Only 60 overlap with the 69 unmatched transactions; nine unmatched transactions already have supported transfer/cash treatment. No classification repair or new AI pass was warranted. Additional source notes and factual clarification are needed before reducing these unknowns safely.

Run `.venv\Scripts\python.exe -m src.classification`. The processor reads both 3A reconciled documents plus `output/relationships/relationships.json`, validates their schemas/provenance and writes `output/classification/effective_transactions.json` atomically. No API calls, new dependencies, repayment matching or settlement logic are involved.

Precedence is explicit:

1. Supported 3B financial treatments override semantic event labels: transfers, cash conversions, reimbursements, principal advances/borrowing, recoverable/shared payments and salary. Matching relationship status/evidence is required; planned and unresolved links do not become executed movements. A source-supported reimbursement receipt may be classified while its expense allocation remains unresolved.
2. Payments on behalf of others and purpose-specific funding with unresolved terms remain financially unknown, even if an AI event calls them purchases/income.
3. Explicit bank fee narration supports an expense independently of missing notes.
4. Otherwise a matched, non-low-confidence, ambiguity-free purchase/service meaning backed by a nonempty note supports an ordinary expense. An explicit set of ordinary semantic types/categories is used; special transfer/loan/cash labels do not silently become purchases. No unmatched debit becomes an expense solely because it is a debit, and no unknown credit becomes income.
5. Everything else remains unknown. Null income/expense flags mean unknown/deferred, not zero. Recoverable/shared gross debits have personal expense allocation deferred; no partial shares are calculated here.

Each output row has the original stable transaction ID and authoritative bank facts, a compact semantic projection, effective treatment with independent confidence/source/rule, relationship references with participant/candidate role and status, review reasons, and source/event fingerprints. Merchant, purpose, category, description and semantic confidence remain exactly as interpreted upstream. This projection is not a corrected semantic event: original labels can disagree with the effective treatment. Full entities, components, matching clues and other event context remain accessible through the linked event ID and hash in the unchanged upstream document. Use `effective` for financial counting, not semantic category.

All 154 bank records remain represented; all 85 matched events are linked one-to-one; 69 records have no matched event. The 130 3B-unclassified movements include 70 with semantic events, all preserved. They now yield 62 expenses and 68 financially unknown movements. Unknown financial treatment is not the same as missing category or absence of a relationship.

Current effective counts: expense 62; self_transfer 6; cash_conversion 6; reimbursement_received 5; recoverable_or_shared_expense 5; loan_advance 1; salary_income 1; unknown 68. Eight rows involve unresolved relationships (including candidate participants), five have deferred personal-expense allocation, and six retain semantic uncertainty. These review groups overlap. Among unknowns, 60 have no event and eight have meanings that do not establish financial treatment. Nine of the 69 rows without events have supported 3B treatments.

Tests and real-data assertions cover Blinkit printouts, own-account allocation, cross-account reimbursements, ATM/cash exchanges, the temporary advance, blank-note uncertainty, planned transfers, incorrect AI labels, source preservation and idempotency. Phase 3C.2 obligations, 3C.3 settlement matching, 3C.4 broader financial integrity and 3D persistence remain out of scope. Review the effective view before proceeding.

## Phase 3C.2: obligation origins

Run `.venv\Scripts\python.exe -m src.obligations`. It reads existing reconciled documents, relationships, effective classifications and the coverage audit, verifies source fingerprints, then atomically writes `output/obligations/obligations.json`. It makes no API calls and does not rewrite previous stages. Run the complete local suite with `.venv\Scripts\python.exe -m unittest discover -s tests`.

The output has `obligations`, `clarification_candidates`, input fingerprints, a principal-only report, and file-preservation evidence. Each obligation references its originating transaction/event IDs and supporting relationships, with stable origin/party/direction identity, counterparty, raw source purpose, original principal (nullable), INR currency basis, confidence, evidence hashes, and lifecycle fields. `origin_status: CONFIRMED` establishes the historical claim; `settlement_status: UNASSESSED` explicitly does not mean it is still open. No settled or outstanding amount is calculated.

Evidence rules:

- Supported 3B advance/borrowing classification supplies repayment intent, overriding erroneous AI repayment labels. Actual bank direction establishes receivable versus payable. Unsupported or partial principal/party details require clarification.
- Reimbursement components are proposals. A party's claim must be supported by the raw note and existing expected-reimbursement relationship; each original amount needs explicit party/share evidence. A whole purchase for a named beneficiary with an explicit promise to repay can support the full expense. The gross amount is never a default shared-expense principal.
- Explicit source `I owe <party>` plus a corresponding payable component supports a payable; an AI-only component does not. Self-transfers, cash conversions, salary and reimbursement receipts do not generate new claims. Planned own-account movements do not create debt.
- Conflicting note/follow-up amounts, unsupported components and receipt amounts that do not establish original principal remain unknown. Duplicate same-party components produce one origin; incompatible or excessive shares require clarification.
- Existing reimbursement/possible-return relationships are retained as referenced context. No new receipt search, matching, deductions, settlement allocations or closures occur.

Current result: eight receivable origins (one advance, seven reimbursement claims), five known principals totaling INR 6,603.00, three unknown principals, and no evidenced payables. **This total is not the currently outstanding balance.** The unknown amounts are Nandy's electricity share (131 versus 131.50) and the two group-meal shares (reported 180/100 do not establish original entitlement alongside the stated one-third personal share).

Six clarification candidates cover those two expense transactions and the four uncertain annotated transactions: KOTAK_0026, KOTAK_0027, KOTAK_0060 and SBI_0009. They contain a question, requested fields, source evidence and a null answer. A future assistant must preserve respondent, timestamp, raw answer, question ID and prior-interpretation hash in an interpretation revision; it must not overwrite authoritative bank facts or original semantic events. No assistant or answer-application mechanism is included here. Missing/blank-note unknowns receive no invented obligations or new prompts.

Counterparties are source-level names, not globally resolved identities. Group counterparties such as parents remain unallocated. Rules are deliberately bounded and may require clarification for different wording; only these owned INR statements are supported. All origins remain UNASSESSED until Phase 3C.3. Stop here for review before settlement implementation.

### Approved settlement tolerance for Phase 3C.3

When strong evidence links repayments to an obligation and supports full settlement, a final residual difference of up to INR 5.00 inclusive may be recorded as a rounding/minor settlement adjustment and the obligation marked settled. Preserve original transaction amounts/references and record actual repayment totals and the signed difference/adjustment separately with evidence. Apply the tolerance to the final obligation residual, never independently to each partial repayment. Do not raise clarification solely for a qualifying minor difference. Similar amounts alone cannot establish a match; unexplained overpayments, genuinely larger remaining debt and uncertain original principal do not qualify. Original 3C.2 records stay UNASSESSED; the separate 3C.3 view holds assessed statuses.

## Phase 3C.3: settlement engine

Run `.venv\Scripts\python.exe -m src.settlements`. It validates the previous stages and original obligation fingerprints, then atomically writes `output/settlements/settlements.json`. The original bank facts, events, relationships, classifications, audit and obligation origins are never rewritten. Full tests: `.venv\Scripts\python.exe -m unittest discover -s tests`.

The single output contains:

- `allocations`: stable obligation/repayment links, actual allocated amounts, matching rules, original-note evidence, source hashes and relationship references.
- `obligation_settlement_view`: original principal, verified repayments, signed adjustment, unadjusted residual, nullable outstanding amount, status, evidence date, allocation IDs and existing clarification references.
- `unresolved_candidates` and `clarification_candidates`: unallocated evidence and unanswered questions, kept separate from verified payments. Existing origin questions are referenced without duplication.
- `classification_effects`: repayment treatment for the allocated amount of each existing bank movement. Apply this overlay when consuming the earlier effective view; never count the allocations as extra cashflows. Original financial labels stay unchanged, unallocated portions are not reclassified, and unexplained excesses require review.
- `report`, input fingerprints and source-file preservation hashes.

Matching prefers confirmed 3B reimbursement evidence. Otherwise it requires positive source repayment language, counterparty and date/order support plus an explicit origin ID/reference or unique loan/debt context. The same-day immediate-advance-return rule additionally requires an exact bank counterparty identifier, equal principal, explicit immediate-return intention, unique origin and no rival return credit. It is not an amount-only match. Named parties are not merged automatically (for example, Dad and the parents group).

One payment may be split only when original source notes explicitly identify allocations, using `<origin transaction ID>: <amount>` or `= <amount>` with repayment/counterparty context. Multiple repayments can contribute to one or several obligations. Duplicate, malformed or excessive allocations remain candidates; transaction capacity is enforced across the entire result. Self-transfers, salary, cash conversions, unrelated credits and planned transfers cannot become repayments. A verified reimbursement receipt may have a separate future-transfer instruction without treating that instruction as executed.

Exact verified repayment of known principal supports CLOSED. A qualifying final difference up to INR 5 can be a separate signed adjustment; strong full-settlement intent must apply to the last repayment, with no invented cross-bank intraday order. Unexplained overpayment remains UNASSESSED. An unknown principal always stays UNASSESSED even when some receipts are verified. OPEN/PARTIAL require an explicit dated source statement, such as `no repayments received as of YYYY-MM-DD` or `as of YYYY-MM-DD, outstanding balance is <amount>`, agreeing with the ledger and extending through the supplied bank-data horizon. This bounded rule avoids treating absent bank matches as evidence that cash/off-bank repayment never occurred. Unsupported wording requires clarification, not an inferred complete history.

Current result: OPEN 0, PARTIAL 0, CLOSED 3, UNASSESSED 5. Verified receipts total INR 3,345.00; adjustments are 0.00. Closed origins are Jeevin's and Manasa's ice-cream shares and Dad's 3,000 advance. The group-meal receipts (100 and 180) are verified but original shares remain unknown. Nandy remains unassessed. The parents' 3,500 receipt is not allocated between ticket principals totaling 3,538; one new clarification retains that uncertainty. Confirmed outstanding totals are 0.00 for both directions, **not proof of no debt**: 3,538 of known principal remains unassessed, plus three unknown principals. The report deliberately separates these unresolved amounts.

No new API calls, dependencies, financial events, database, assistant or UI. Phase 3C.4 financial integrity verification remains the next slice; stop after this checkpoint for review.

## Phase 3C.4: financial integrity audit

Run `.venv\Scripts\python.exe -m src.integrity` to write `output/audit/financial_integrity.json`. It reports each check as PASS/FAIL, returns a nonzero exit code on failed checks, and records unresolved questions and migration conditions. It reads all financial layers without rewriting them, reparses original bank/notes files, verifies coverage and source hashes, replays deterministic stages and independently validates repayment capacity, allocation uniqueness, exact arithmetic, outstanding balances and classification overlays. Run the full regression suite with `.venv\Scripts\python.exe -m unittest discover -s tests`.

Current verification: 14 checks passed, zero failed; 142 tests passed. All 154 transactions and 85 meanings survive; the 69 missing notes rows are a source-coverage limitation. The 152 adjacent bank balances agree exactly and no duplicate bank facts were found. Existing uncertainty remains intact: the train-ticket allocation, Nandy/group-meal original amounts, and four annotated financial-term ambiguities. Zero confirmed outstanding does not imply zero debt. The first opening balance is not independently established by the adjacent-balance test, and absent cash/off-bank history is not presumed complete.

Settlement schema `3C.3.2` distinguishes `evidence_strength` from `confirmation_method`. Dad's 3,000 repayment remains CLOSED with strength `high` and method `inferred`. `source_supported` and `explicit_source_allocation` identify other methods; none of these fields implies explicit confirmation by a user or bank. The existing confidence field remains compatible, and the settlement view separately records repayment strength and methods. Preserve those distinctions in future database columns.

Three audited corrections strengthen the engine: an explicit dated positive balance blocks a small write-off; full-settlement intent in an explicit split applies only to its origin clause (unless all listed obligations are explicitly covered); and downstream audit reports are excluded from upstream dependency fingerprints. Original financial data is unchanged. Only the settlement metadata/schema was regenerated; real allocations, amounts and statuses remain unchanged. Audit and settlement reruns remain byte-identical even after the new audit report exists.

The model is ready to migrate with nullable unknown amounts, distinct UNASSESSED status, source hashes, stable unique identifiers, allocation-capacity checks and the classification overlay preserved. Migration readiness does not mean financial questions are resolved or all history is known. No PostgreSQL implementation is included; stop before 3D for review.
