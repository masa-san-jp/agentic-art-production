#!/usr/bin/env python3
"""Replay or append lifecycle events for a materialized production project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.runtime import Runtime


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _finding(rule: str, reason: str, *, file: Path | str, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), remediation=remediation)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path, help="Git-external production project")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay = subparsers.add_parser("replay", help="replay the append-only event log and verify the projection")
    replay.set_defaults(command="replay")
    bootstrap = subparsers.add_parser("bootstrap", help="create the initial HANDOFF_VALIDATED -> PLANNING event when needed")
    bootstrap.add_argument("--occurred-at", required=True)
    bootstrap.add_argument("--actor-kind", choices=("AGENT", "HUMAN", "SYSTEM"), default="SYSTEM")
    bootstrap.add_argument("--actor-id", required=True)
    bootstrap.add_argument("--idempotency-key", default="runtime/bootstrap/1")
    transition = subparsers.add_parser("transition", help="append one guarded lifecycle transition")
    transition.add_argument("--to-state", required=True)
    transition.add_argument("--occurred-at", required=True)
    transition.add_argument("--actor-kind", choices=("AGENT", "HUMAN", "SYSTEM"), required=True)
    transition.add_argument("--actor-id", required=True)
    transition.add_argument("--idempotency-key", required=True)
    transition.add_argument("--reason", required=True)
    transition.add_argument("--payload-json", default="{}")
    graph = subparsers.add_parser("init-tasks", help="register the validated production plan task graph")
    graph.add_argument("--occurred-at", required=True)
    graph.add_argument("--actor-kind", choices=("AGENT", "HUMAN", "SYSTEM"), default="SYSTEM")
    graph.add_argument("--actor-id", required=True)
    graph.add_argument("--idempotency-key", default="runtime/task-graph/1")
    next_task = subparsers.add_parser("next-task", help="select the deterministic next eligible task without claiming it")
    next_task.add_argument("--occurred-at", required=True)
    claim = subparsers.add_parser("claim", help="claim one eligible task with a lease")
    claim.add_argument("--task-id")
    claim.add_argument("--occurred-at", required=True)
    claim.add_argument("--actor-id", required=True)
    claim.add_argument("--lease-token", required=True)
    claim.add_argument("--expires-at", required=True)
    claim.add_argument("--idempotency-key", required=True)
    heartbeat = subparsers.add_parser("heartbeat", help="extend an active task lease")
    heartbeat.add_argument("--task-id", required=True)
    heartbeat.add_argument("--occurred-at", required=True)
    heartbeat.add_argument("--actor-id", required=True)
    heartbeat.add_argument("--lease-token", required=True)
    heartbeat.add_argument("--expires-at", required=True)
    heartbeat.add_argument("--idempotency-key", required=True)
    recover = subparsers.add_parser("recover", help="recover one expired task lease after effect reconciliation")
    recover.add_argument("--task-id", required=True)
    recover.add_argument("--occurred-at", required=True)
    recover.add_argument("--idempotency-key", required=True)
    approval = subparsers.add_parser("approve", help="append a schema-valid approval record")
    approval.add_argument("--approval-json", required=True)
    approval.add_argument("--occurred-at", required=True)
    approval.add_argument("--actor-kind", choices=("AGENT", "HUMAN", "SYSTEM"), required=True)
    approval.add_argument("--actor-id", required=True)
    approval.add_argument("--idempotency-key", required=True)
    effect_start = subparsers.add_parser("effect-start", help="record an effect intent after approval validation")
    effect_start.add_argument("--task-id", required=True)
    effect_start.add_argument("--occurred-at", required=True)
    effect_start.add_argument("--actor-id", required=True)
    effect_start.add_argument("--lease-token", required=True)
    effect_start.add_argument("--effect-key", required=True)
    effect_start.add_argument("--target-ref", required=True)
    effect_start.add_argument("--target-sha256", required=True)
    effect_start.add_argument("--idempotency-key", required=True)
    effect_complete = subparsers.add_parser("effect-complete", help="record the observed result of an effect")
    effect_complete.add_argument("--effect-key", required=True)
    effect_complete.add_argument("--task-id", required=True)
    effect_complete.add_argument("--occurred-at", required=True)
    effect_complete.add_argument("--actor-id", required=True)
    effect_complete.add_argument("--lease-token", required=True)
    effect_complete.add_argument("--status", choices=("SUCCEEDED", "FAILED", "UNKNOWN"), required=True)
    effect_complete.add_argument("--evidence-refs-json", default="[]")
    effect_complete.add_argument("--error-class")
    effect_complete.add_argument("--idempotency-key", required=True)
    retry = subparsers.add_parser("retry", help="schedule a transient task retry")
    retry.add_argument("--task-id", required=True)
    retry.add_argument("--occurred-at", required=True)
    retry.add_argument("--actor-id", required=True)
    retry.add_argument("--lease-token", required=True)
    retry.add_argument("--retry-after", required=True)
    retry.add_argument("--reason", required=True)
    retry.add_argument("--idempotency-key", required=True)
    fail = subparsers.add_parser("fail", help="record a permanent task failure")
    fail.add_argument("--task-id", required=True)
    fail.add_argument("--occurred-at", required=True)
    fail.add_argument("--actor-id", required=True)
    fail.add_argument("--lease-token", required=True)
    fail.add_argument("--reason", required=True)
    fail.add_argument("--idempotency-key", required=True)
    complete = subparsers.add_parser("complete-task", help="mark a task done after evidence and effects are complete")
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--occurred-at", required=True)
    complete.add_argument("--actor-id", required=True)
    complete.add_argument("--lease-token", required=True)
    complete.add_argument("--evidence-refs-json", required=True)
    complete.add_argument("--idempotency-key", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = Runtime(args.project_root, repository_root())
    try:
        if args.command == "replay":
            state = runtime.replay()
        elif args.command == "bootstrap":
            state = runtime.bootstrap(occurred_at=args.occurred_at, actor_kind=args.actor_kind, actor_id=args.actor_id, idempotency_key=args.idempotency_key)
        elif args.command == "transition":
            try:
                payload: dict[str, Any] = json.loads(args.payload_json)
            except json.JSONDecodeError as exc:
                raise DiagnosticError(_finding("RUNTIME_PAYLOAD_JSON", str(exc), file="--payload-json", remediation="Pass one JSON object as --payload-json.")) from exc
            if not isinstance(payload, dict):
                raise DiagnosticError(_finding("RUNTIME_PAYLOAD_OBJECT", "--payload-json must decode to a JSON object", file="--payload-json", remediation="Pass a JSON object containing transition guard evidence."))
            state = runtime.transition(to_state=args.to_state, occurred_at=args.occurred_at, actor_kind=args.actor_kind, actor_id=args.actor_id, idempotency_key=args.idempotency_key, reason=args.reason, payload=payload)
        elif args.command == "init-tasks":
            state = runtime.initialize_task_graph(occurred_at=args.occurred_at, actor_kind=args.actor_kind, actor_id=args.actor_id, idempotency_key=args.idempotency_key)
        elif args.command == "next-task":
            state = {"task_id": runtime.next_task(occurred_at=args.occurred_at)}
        elif args.command == "claim":
            state = runtime.claim_task(task_id=args.task_id, occurred_at=args.occurred_at, actor_id=args.actor_id, lease_token=args.lease_token, expires_at=args.expires_at, idempotency_key=args.idempotency_key)
        elif args.command == "heartbeat":
            state = runtime.heartbeat(task_id=args.task_id, occurred_at=args.occurred_at, actor_id=args.actor_id, lease_token=args.lease_token, expires_at=args.expires_at, idempotency_key=args.idempotency_key)
        elif args.command == "recover":
            state = runtime.recover_expired_lease(task_id=args.task_id, occurred_at=args.occurred_at, idempotency_key=args.idempotency_key)
        elif args.command == "approve":
            approval = json.loads(args.approval_json)
            if not isinstance(approval, dict):
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_OBJECT", "--approval-json must decode to an object", file="--approval-json", remediation="Pass one approval record object."))
            state = runtime.record_approval(approval=approval, occurred_at=args.occurred_at, actor_kind=args.actor_kind, actor_id=args.actor_id, idempotency_key=args.idempotency_key)
        elif args.command == "effect-start":
            state = runtime.start_effect(task_id=args.task_id, occurred_at=args.occurred_at, actor_id=args.actor_id, lease_token=args.lease_token, effect_key=args.effect_key, target_ref=args.target_ref, target_sha256=args.target_sha256, idempotency_key=args.idempotency_key)
        elif args.command == "effect-complete":
            evidence_refs = json.loads(args.evidence_refs_json)
            if not isinstance(evidence_refs, list) or not all(isinstance(item, str) for item in evidence_refs):
                raise DiagnosticError(_finding("RUNTIME_EVIDENCE_OBJECT", "--evidence-refs-json must decode to a list of strings", file="--evidence-refs-json", remediation="Pass a JSON array of evidence references."))
            state = runtime.complete_effect(effect_key=args.effect_key, task_id=args.task_id, occurred_at=args.occurred_at, actor_id=args.actor_id, lease_token=args.lease_token, status=args.status, evidence_refs=evidence_refs, idempotency_key=args.idempotency_key, error_class=args.error_class)
        elif args.command == "retry":
            state = runtime.retry_task(task_id=args.task_id, occurred_at=args.occurred_at, actor_id=args.actor_id, lease_token=args.lease_token, retry_after=args.retry_after, reason=args.reason, idempotency_key=args.idempotency_key)
        elif args.command == "fail":
            state = runtime.fail_task(task_id=args.task_id, occurred_at=args.occurred_at, actor_id=args.actor_id, lease_token=args.lease_token, reason=args.reason, idempotency_key=args.idempotency_key)
        elif args.command == "complete-task":
            evidence_refs = json.loads(args.evidence_refs_json)
            if not isinstance(evidence_refs, list) or not all(isinstance(item, str) for item in evidence_refs):
                raise DiagnosticError(_finding("RUNTIME_EVIDENCE_OBJECT", "--evidence-refs-json must decode to a list of strings", file="--evidence-refs-json", remediation="Pass a JSON array of evidence references."))
            state = runtime.complete_task(task_id=args.task_id, occurred_at=args.occurred_at, actor_id=args.actor_id, lease_token=args.lease_token, evidence_refs=evidence_refs, idempotency_key=args.idempotency_key)
        else:  # pragma: no cover - argparse enforces command values
            raise DiagnosticError(_finding("RUNTIME_COMMAND", f"unsupported runtime command {args.command!r}", file="--command", remediation="Choose a supported runtime command."))
        print(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2))
        return EXIT_SUCCESS
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format=args.format)
        return EXIT_VALIDATION
    except (OSError, KeyError, TypeError, ValueError) as exc:
        emit_findings([_finding("RUNTIME_CLI", str(exc), file=args.project_root, remediation="Correct the runtime input and retry.")], output_format=args.format)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
