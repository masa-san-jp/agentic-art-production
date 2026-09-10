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
c97302b0a0ea926ed034f5ed53d0122905f67f27 and remote candidate
8824ea7746c35c05ff25c7ac24cf42768888c131 share exact tree
feb5dd09ef59daefcb82bf050773459406ad95c2 (every blob/tree verified).
Focused attestation/visual tests 11 PASS; full 104 tests PASS in 490.816s;
native validator, evaluation (all six checks) and diff PASS. Full log SHA-256
9765e0b1e5d50424db7ef2a2922b09b464b4ccf21a1a0a69bfcd52191ad0629c;
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

Canonical `PLAN_READY` producers use the closed automatic plan lane instead:

```bash
.venv/bin/python tools/public_plan_attestation.py --project-root "$PROJECT_ROOT" \
  --automatic-plan --producer-commit "$PRODUCER_COMMIT" --generated-at "$GENERATED_AT"
```

This lane performs the same renderer, content, and asset checks and records
`publication_review.authority: AUTOMATIC_PLAN` with a deterministic
`automatic-plan-authority/<project>#<plan>` reference. It clears only the
plan record for the automatic catalog projection; `external_effects_authorized`
remains `false`. It does not create a human approval, consent, purchase,
physical action, Git operation, or remote publication. Work/manual requests
continue to use `--review` or `--native-review` and the existing human gate.

```bash
.venv/bin/python tools/public_plan_attestation.py --project-root "$PROJECT_ROOT" \
  --review "$PUBLIC_REVIEW" --producer-commit "$PRODUCER_COMMIT" \
  --generated-at "$GENERATED_AT"
.venv/bin/python tools/public_plan_attestation.py --project-root "$PROJECT_ROOT" --check
```

The producer commit must be the clean executing checkout. Generation validates
the project. The successful --check JSON also returns the validated aggregate's
production_state so receivers do not infer it from PLAN_READY or parse owner YAML.
This state is descriptive and does not authorize physical or external effects.
Generation renders in memory; public Markdown stays byte-identical. Native
project validation also checks an existing attestation. JSON canonicalization is
the existing json-sort-keys-compact-utf8-v1; only the integrity field is omitted
when computing its self-hash. Coverage names semantic aggregate fields, never
Markdown headings. Physical safety review remains distinct from content clearance.
Attestation errors never approve external effects. A receiver must qualify the
producer commit and bind the no-transform projection receipt before trusting this
unsigned integrity record; recomputing a hash is not proof of producer identity.

Production owns attestation/v1. Orchestration #193 owns no-transform projection/v2;
Project #6 consumes both. No downstream completion is a prerequisite of #60.

The qualified extension returns production_state from the owner aggregate through
CLI --check. Five focused attestation tests PASS; the full 104 and six evaluation
checks PASS again. Parent #193 actual owner-boundary synthetic test PASS (exact
body/attestation/assets, replay, tamper rejection); this is not live AAK-02 proof.
