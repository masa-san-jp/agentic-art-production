# Evidence v1 migration

`EVIDENCE-INGEST-001` introduces the v1 evidence wire contract. It is a breaking change for canonical `evidence_refs`: URI strings are no longer accepted. Canonical records must use exactly `{evidence_id, revision}`; the URI is stored only in the registered evidence record.

## Migration rule

Migration is additive and fail-closed:

1. Preserve the original execution/runtime/prototype log as an immutable backup.
2. For every old URI reference, create a new `EVD###` metadata record with the exact target ID, opaque URI, content SHA-256, capture/record timestamps, rights/privacy status, verification method, and limitations.
3. Record the metadata through `tools/run_execution.py record-evidence`; do not copy the referenced body into the project or repository.
4. Replace the dependent canonical record in a new revision with `{"evidence_id":"EVD###","revision":1}` and replay all projections.
5. A reference to PENDING or REJECTED evidence, an unregistered reference, a target mismatch, or a mixed URI/object list remains invalid.

There is no in-place conversion of an append-only event. If the old event cannot be superseded by a new canonical revision with preserved provenance, the project remains `BLOCKED` and requires an authorized human migration decision.

## Compatibility

The schema files are v1.0.0. Evidence log events use `EVIDENCE_RECORDED`; evidence projection uses `evidence-register.yaml`. The existing production-result consumer contract continues to expose its output `evidence_ref` URI field; result generation resolves canonical evidence objects back to the registered opaque URI at that boundary.
