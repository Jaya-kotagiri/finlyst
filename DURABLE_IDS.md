# Durable financial identities (Phase 3R.2C)

`src/identities.py` adds a persistent identity registry alongside the existing JSON
pipeline. `src/identity_models.py` validates registry version `3R.2C.1`. Existing
financial files, schemas, legacy IDs, calculations and stage commands are unchanged.
The private registry is `output/identities/identity_registry.json` and is ignored by Git.

## Audit of the existing identifiers

| Record | Existing identity | Problem across imports or corrections |
| --- | --- | --- |
| Bank transaction | Bank prefix plus row sequence | Restarts for another file/account/month; overlap can renumber the same payment. |
| Semantic event | Bank prefix plus event sequence | Restarts for each notes file; row insertion can change the number. |
| Relationship | Hash of type, legacy participants, status and rule | Rule/status changes can rename a relationship; legacy participants are file-scoped. |
| Obligation | Hash of legacy origin, direction and counterparty label | Name corrections can rename the same claim; labels are not person identities. |
| Allocation | Hash of legacy repayment and obligation | Inherits identity changes from both parents. |
| Settlement view | Legacy obligation ID | Inherits the obligation's identity instability. |
| Candidate / clarification | Hash including legacy links and reason | Changed evidence/reason can rename the same review subject. |

These identifiers remain valid within their original artifacts. They must not be
used as globally unique PostgreSQL primary keys.

## Permanent records, revisions and provenance

- A permanent record has an opaque UUID, record kind and immutable birth seed.
  UUIDv5 uses a fixed project namespace and canonical SHA-256 inputs. Identity
  canonicalization is separate from existing financial fingerprints.
- Bank account identity uses the bank and full account number. Transaction identity
  initially uses the account and complete normalized bank facts, excluding row ID.
- Derived identities are allocated once from their first source observation, then
  retained in the registry. Later matching uses durable parents and recorded aliases,
  not a freshly computed hash of the current name, status, rule or interpretation.
- An interpretation revision has its own UUID, content fingerprint and durable
  references. A changed interpretation or parent reference creates a revision of
  the same confirmed record. Repeated identical observations reuse revisions.
- A source has content/document hashes, locations and upstream source-file hashes.
  Paths are provenance, not permanent identity. Formatting changes can create a new
  source observation without creating another transaction.
- Every legacy mapping is scoped by **source ID + record kind + legacy ID**. It
  retains the original record fingerprint, identity evidence and decision history.
  Never resolve a bare `KOTAK_0001` across files. Reconciliation, classification and
  audit rows alias the original transaction rather than creating extra cashflows.

Keep and privately back up this registry across imports. Rebuilding it from a
different first snapshot is not a substitute for retaining previously allocated
derived UUIDs. The registry contains references and fingerprints, not replacement
copies of financial records. Retain original source/snapshot artifacts as well;
this slice does not implement a financial-history archive or select a latest revision.

## Conservative overlap and continuity rules

1. A statement must describe one account. Current inspected account values are full
   numbers; automatic account identification accepts unmasked digits of at least
   eight characters. Missing, masked or short values remain unresolved. This is a
   contract for these sources, not a universal account-number validator or person matcher.
2. Exact complete bank facts, an established account, and a reference or running
   balance support a unique overlap match. A new statement's sequence number and
   filename do not affect that match. SBI's missing references can use its unique
   narration/balance evidence. Amount/date similarity alone is insufficient.
3. Repeated indistinguishable occurrences, competing identities or conflicting
   reference/fact evidence remain unresolved; no speculative second transaction is
   created. A smaller overlapping statement cannot erase earlier occurrence ambiguity.
   All source rows remain represented by mappings, including unresolved ones.
4. Derived records use durable parent slots: a primary event's bank transaction,
   relationship participants, a claim's originating transactions, an allocation's
   obligation and repayment, or a settlement view's obligation. A unique existing
   slot can survive changed status/type/rule. Multiple slots are preserved separately;
   competing continuities require clarification rather than elimination guessing.
5. A claim can acquire another claimant, so one existing claim alone does not prove
   a changed name is the same person. An unchanged source party label/direction
   within the same durable origin supports overlap continuity; a name correction
   requires an explicit decision. Labels are supporting evidence, never global
   counterparty IDs. Matching existing legacy aliases is also scoped to durable parents.
6. An explicit continuity decision records `actor`, `reason` and `target_id`. A known
   target preserves its UUID and adds evidence/revisions. A null target explicitly
   confirms a distinct new record; it is not an unknown answer. Repeating a decision
   is idempotent. An already resolved source observation cannot be reassigned.
7. An unmatched event or unresolved parent retains an unresolved mapping; downstream
   identity is not guessed. Zero identity ambiguity on the current snapshot does not
   resolve its financial uncertainties, unknown principals or UNASSESSED settlements.

Changed parent sets may describe genuinely new records. If they are corrections to
existing records, callers must supply explicit continuity; the registry cannot infer
that intention. Likewise, similar-looking genuine extra payments may need identity
clarification. No automatic merging of accounts, names or uncertain records occurs.

## Python integration

Phase 3R.3 exposes these functions through `python -m src.cli identities` and checks
saved mappings without writing through `python -m src.cli validate --all`. Existing
financial writers continue to produce their legacy formats. See README for pipeline usage.

```python
from src.identities import empty_registry, import_bank, lookup, migrate, run

# Current verified local pipeline: validates/replays stages, then writes only the registry.
registry = run()

# Additional normalized statement: pure function, returns new state and source scope.
registry, source = import_bank(registry, normalized_rows, metadata=source_metadata)
decision = lookup(registry, source, "transaction", normalized_rows[0]["transaction_id"])

# A coherent bank/event/downstream snapshot can be migrated in memory as a unit.
registry = migrate(snapshot, registry, metadata=stage_metadata, continuity=decisions)
```

`source_id()` and `observation_id()` compute the exact scope for a continuity decision.
Use the same content hash metadata when computing the scope and importing. See the
synthetic tests for complete examples. `import_bank`/`migrate` never mutate their inputs.
`validate_registry` checks versions, UUIDs, hashes, aliases, revision consistency and
cross-record references. The file runner validates before atomically replacing the
registry and rejects detected concurrent changes. It is a **single-writer** operation,
not a transactional multi-process service.

The existing financial pipeline handles one document per bank per snapshot. Import
additional account/month snapshots separately into the retained registry; do not
concatenate legacy documents with colliding row IDs. Consumers of overlapping imports
must join through these mappings and count distinct resolved transaction UUIDs, not
sum source occurrences, projections or settlement records. Unresolved occurrences
remain visible for review and cannot safely be included in a deduplicated total.

## PostgreSQL migration design (not implemented)

- Use permanent record UUIDs as primary keys and durable references as foreign keys.
  Keep record kind separate from mutable financial type/status.
- Store source artifacts and legacy occurrences separately, with a unique constraint
  on `(source_id, kind, legacy_id)`. Multiple imports can reference one transaction.
- Store revisions separately from records, with their content hashes, reference edges
  and source occurrence links. Explicitly select the applicable snapshot/revision;
  do not sum all historical revisions or treat insertion order as financial authority.
- Preserve identity decision history, unresolved candidates and explicit continuity
  provenance. Evidence hashes are lookup indexes, not universally unique constraints:
  indistinguishable genuine occurrences can share them.
- Implement transactional upserts/locking in 3D before concurrent writers. Retain
  existing financial capacity checks, nullable amounts and UNASSESSED states.

## Verification

The migration maps 154 transactions, 85 events, 27 relationships, eight obligations,
five allocations and eight settlement views, plus two accounts, six origin questions,
one settlement candidate and one settlement question. There are 297 permanent records,
297 revisions, 11 source artifacts and 844 scoped legacy mappings; all current mappings
resolve. Repeated runs produce byte-identical registry output.

The 27 synthetic regressions cover repeated/overlapping imports, reversed import order,
accounts/months, renumbering, missing evidence, indistinguishable occurrences, explicit
continuity, counterparty/status/rule changes, shared claims, cross-record references,
invalid registry data and rejection before overwriting a valid output. All 165 earlier
tests remain unchanged. No new API calls, dependencies or financial-rule changes.
