# Curated Production knowledge

AAK-11 extends protocol-only ownership solely for validated, curated knowledge under `knowledge/production/` in an explicit owner Git store. Runtime projects, logs, large works, raw files, contracts and personal data stay in their existing external locations. The code checkout and knowledge Git histories are separate; no upstream write, purchase, physical work or public release is performed by these commands.

## Store and capture

Use a new explicit absolute Git store path outside the code checkout. Creator, origin instance and collection are explicit; opening a different creator/collection is rejected. The store is local and private to its creator; `CREATOR_PRIVATE`/`PROJECT_INTERNAL` records are not automatically published or treated as shared knowledge.

```bash
.venv/bin/python tools/production_memory.py --store /absolute/owner-memory.git --creator creator-a --collection production-a init --instance instance-a
.venv/bin/python tools/production_memory.py --store /absolute/owner-memory.git --creator creator-a --collection production-a ingest --project /absolute/output/production/example --request /absolute/request.yaml --operation capture-one --run-id run-one --parent FULL_KNOWLEDGE_COMMIT
```

The request follows `schemas/production-knowledge-request.schema.json`. It selects a stable record ID/revision, native task and observation IDs, knowledge kind, source conditions, an explicitly inferred interpretation and proposed future use. `planned`, `simulated`, `prototyped` and `observed` remain distinct. Proposed estimates never become actual measurements. Observed/prototyped claims require native evidence references and reject explicitly synthetic observations; prototyped claims also reference the native prototype. Native project validation checks the observation/evidence ledger and result integrity. A selected observation must match the latest result exactly. Source hashes, native observation revisions, result reference and proposed procedure are retained; runtime ownership does not move into the store.

No selected observation in a non-planned capture yields `NO_NEW_EVIDENCE`/`NO_CHANGE`; this does not block an otherwise complete plan. A planned method can be retained separately as proposed knowledge. Unknown privacy status, raw/security markers, large payloads, mismatching task/result references and invalid native schemas are rejected. The source declarations and evidence registration do not establish external truth or grant permission to perform the proposed work.

Records use the shared `artifact-record/v1` envelope and writes return `knowledge-write-receipt/v1`. Commit uses compare-and-swap against the expected parent; retries of the same record/revision/content return the original commit. Conflicting revisions or operation IDs are rejected. Index failure returns `INDEX_PENDING` while preserving the committed record; replay reindexes that snapshot. The disposable index is outside Git, and query reads the pinned Git content even after index removal/restart. Revisions append and supersede earlier versions without deleting them.

## Plan reuse and correction

```bash
.venv/bin/python tools/production_memory.py --store /absolute/owner-memory.git --creator creator-a --collection production-a query --snapshot FULL_KNOWLEDGE_COMMIT --project-id production/next --environment /absolute/environment.yaml --at 2026-09-08T00:00:00Z
```

The environment declares equipment, skill, size, safety, currency and `as_of`. Mismatches are `NOT_APPLICABLE`; revoked/expired sources, unknown conditions or a different comparison date require `REVALIDATE`. Same-project revision and cross-project reuse are distinct. A retracted observation is captured as a new revoked revision, retaining its history.

An external agent places `02_specification/production-memory-query.yaml` in the external target project. Its closed fields are `contract_version: production-memory-query-request/v1`, `store`, `creator`, `collection`, `snapshot`, `at`, `environment`, and `selections`. Each selection has `record_id`, `target_task_id`, and a concrete `reason`. The environment must equal the proposed production method's environment. The store path stays out of the aggregate; code and knowledge commits remain separate. `build_plan.py` resolves each selected record, records adoption/rejection/revalidation, and adds an adopted use proposal and reason to the actual proposed step. The native validator independently reconstructs that projection and rejects forged selections. Retrieval alone is not adoption. Rejected candidates do not alter the method, and missing new observations do not fabricate actuals.

After qualification, persist the actual adopted references:

```bash
.venv/bin/python tools/production_memory.py --store /absolute/owner-memory.git --creator creator-a --collection production-a remember-use --project /absolute/output/production/next --parent FULL_KNOWLEDGE_COMMIT
```

This append-only use record binds source IDs/revisions/hashes to the target plan hash and task decisions. Later corrections or retractions enumerate affected projects for revalidation. An already changed source cannot be newly recorded as valid reuse. Historical snapshots remain reproducible; a new run pins a new knowledge snapshot to observe corrections. No historical plan or creator attribution is rewritten automatically.

AAK-10 content readiness, execution readiness and publication attestation remain independent. A private reuse plan is not public-cleared merely because its content is actionable. The external agent still resolves any public-review/human gate before publication.

Validation: `tests.test_production_memory` exercises native synthetic prototype observations/result capture, real Git restart/retrieval, actual separate-plan step changes, incompatible conditions, planned/actual separation, idempotency/corrections, zero observations, affected-plan revalidation, creator isolation and index failure/retry. It is synthetic regression evidence, not AAK-02 live acceptance or physical production.
