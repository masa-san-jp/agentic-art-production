# Public plan attestation

## Purpose / Big Picture

Issue #60 owns production-public-plan-attestation/v1 so receivers can verify the
complete renderer output without copying Production headings or planning schemas.
This is an AAK prerequisite at SPEC/PLAN b0e7c7f8d0a1f756fa708deef4fb380a62e45e0d.

## Progress

CODE_VALIDATED from clean base 9d125fb87be133f5e73e61e04e23c0a3bfafb462 on
agent/production-60-public-attestation. No existing attestation implementation or
open Production PR was found. Current Project PR9 still uses projection/v1 and
heading checks; it is not qualification for the current Issue #6 contract.

## Surprises & Discoveries

Generated visual assets are PROJECT_INTERNAL. This does not imply public rights.
Physical REVIEW_REQUIRED is distinct from content publication clearance.
Project #6 requires media/ under each public plan. New Production renders use
03_plan/media/ so the exact same Markdown links resolve after projection without
rewriting. Existing internal visual-package/ records remain schema-readable;
public attestation requests their regeneration by Production, never receiver
rewriting. No actual project is regenerated or moved by this code change.

## Decision Log

Use the native aggregate/project validator and native renderer. Require explicit,
hash-bound content, rights and consent clearance; do not infer it from successful
rendering. Generation writes only the requested local attestation, never publishes.
Preserve the native human gates and all plan bytes. Receiver trust must also pin
the producer code and projection receipt; an integrity hash is not a signature.

## Outcomes & Retrospective

Issue #60 synthetic acceptance PASS. Qualified local code
65a58acb2e07f9bf436777e77343739b332fe1e1 and remote candidate
d323b92ffef34fc80b2e0c47daa1b8acff40368a share exact tree
de622562cd57c47ba363e1d4487ce566566c0c2a (every blob/tree verified).
Focused attestation/visual tests 11 PASS; full 104 tests PASS in 484.673s;
native validator, evaluation (all six checks) and diff PASS. Full log SHA-256
3848bbc3a02bdc9ec50350715df9e1c6d28aa8450384c411e97b7a3c43be038d;
evaluation SHA-256 ba2e1d4c78669d6f0947007000ce291e436daf42448c8805520587fe9c2b003b.

Tests observe exact re-render, schema/integrity/asset hashes, deterministic repeat,
CLI check/replay, one-byte/summary/newline/revision/asset/rights failures and a
changed owner heading without receiver heading knowledge. Initial text-scanner
false positives for prose slash and SVG closing syntax, and an existing hardcoded
renderer image subdirectory, were corrected before final qualification. Earlier
verification runs are superseded; no failure was waived.

The actual synthetic owner output was copied byte-for-byte into Project #6's
receiver fixture and passes its six focused tests. Public body SHA-256
aa4a5dc01396d62a1c74f8a9c7b7a926f457ca2d10b3aa97b6218adf337b556f.
The fixture's explicit rights/consent is synthetic only. No real Production plan,
profile, public migration, publication or physical effect was performed.
Orchestration193 end-to-end projection and real Project migration remain NOT_RUN.
Resume at orchestration Issue193 with this pinned candidate; Project6 retains
its failed current-data migration gates and must not be called complete.

## Context and Orientation

Extend tools/build_plan.py through a separate owner CLI, tools/lib/planning.py and
tools/validate.py validation APIs, schemas/ and synthetic tests. Existing aggregate
and visual-package contracts remain canonical.

## Plan of Work

Define closed JSON attestation/review schemas, implement deterministic render and
manifest verification, test byte/revision/asset/rights failures, synchronize docs.

## Concrete Steps / Validation and Acceptance

Run .venv/bin/python tools/validate.py --check, .venv/bin/python -m unittest discover
-s tests -v, .venv/bin/python tools/run_evaluation.py --format json and git diff
--check. Include valid changed-heading rendering without receiver heading checks.

## Idempotence and Recovery

Identical bytes are replay-safe; a different existing attestation is a conflict.
Reject before writing on any validation failure. Keep private/raw projects outside
the protocol Git tree and retain no actual person's review or consent in fixtures.

## Interfaces and Dependencies

Generation uses an explicitly supplied external `public-plan-review/v1` JSON:
content_safety, rights and consent must each be PASSED, with an opaque consent_ref;
aggregate_sha256 and body_sha256 bind the review to exact bytes. Each linked asset
requires path, SHA-256, byte length, actual MIME, PUBLIC_CLEARED and rights_ref.
PROJECT_INTERNAL alone does not satisfy that review. Review creation/authorization
remains the existing human policy; this CLI never manufactures a review.

```bash
.venv/bin/python tools/public_plan_attestation.py --project-root "$PROJECT_ROOT" \
  --review "$PUBLIC_REVIEW" --producer-commit "$PRODUCER_COMMIT" \
  --generated-at "$GENERATED_AT"
.venv/bin/python tools/public_plan_attestation.py --project-root "$PROJECT_ROOT" --check
```

The producer commit must be the clean executing checkout. Generation validates
the project then renders in memory; public Markdown stays byte-identical. Native
project validation also checks an existing attestation. JSON canonicalization is
the existing json-sort-keys-compact-utf8-v1; only the integrity field is omitted
when computing its self-hash. Coverage names semantic aggregate fields, never
Markdown headings. Physical safety review remains distinct from content clearance.
Attestation errors never approve external effects. A receiver must qualify the
producer commit and bind the no-transform projection receipt before trusting this
unsigned integrity record; recomputing a hash is not proof of producer identity.

Production owns attestation/v1. Orchestration #193 owns no-transform projection/v2;
Project #6 consumes both. No downstream completion is a prerequisite of #60.
