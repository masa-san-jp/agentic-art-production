#!/usr/bin/env python3
"""Start and operate the bounded, proposal-only agent execution harness.

Each run owns one runtime task lease and one immutable task context.  Worker
output is recorded as an action proposal; it never performs a lifecycle
transition or an external effect by itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.agent_harness import HarnessStore, _atomic_json, _format_timestamp, _integrity, _load_plan_task, apply_actions, build_context, create_run, run_invocation, update_run
from tools.lib.config import load_config
from tools.lib.diagnostics import DiagnosticError, EXIT_APPROVAL_REQUIRED, EXIT_EXTERNAL_BLOCKED, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.runtime import Runtime
from tools.lib.yaml_io import load_json


REPOSITORY = Path(__file__).resolve().parents[1]


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset")
    return parsed


def _at(value: str, seconds: int) -> str:
    return _format_timestamp(_time(value) + timedelta(seconds=seconds))


def _token(run_id: str, task_id: str, attempt: int) -> str:
    # This is a deterministic offline fixture lease. Real deployments must
    # supply a secret lease from the canonical runtime issuer.
    return f"harness/{run_id}/{task_id}/{attempt}"


def _actor(run_id: str, actor_id: str | None) -> str:
    return actor_id or f"agent/{run_id}"


def _sync_run(project: Path, run_id: str, *, status: str | None = None, reason: str | None = None, updated_at: str | None = None) -> dict[str, Any]:
    store = HarnessStore(project, REPOSITORY)
    run = store.read_run(run_id)
    state = store.replay(run_id)
    run["status"] = status or run["status"]
    run["updated_at"] = updated_at or run["updated_at"]
    run["last_event_id"] = state["last_event_id"]
    run["last_event_sha256"] = state["last_event_sha256"]
    run["usage"] = {
        "invocation_count": state["invocation_count"],
        "step_count": state["step"],
        "wall_seconds": state["wall_seconds"],
        "input_bytes": state["input_bytes"],
        "output_bytes": state["output_bytes"],
    }
    if reason:
        run["reason"] = reason
    run = _integrity(run)
    from tools.lib.agent_harness import _schema_validate

    _schema_validate(run, REPOSITORY, "agent-run.schema.json")
    _atomic_json(store.path(f"runs/{run_id}.json"), run)
    return run


def _ensure_graph(project: Path, started_at: str, actor_id: str) -> Runtime:
    runtime = Runtime(project, REPOSITORY)
    runtime.bootstrap(occurred_at=started_at, actor_kind="SYSTEM", actor_id="agent-harness/bootstrap")
    state = runtime.replay()
    if not state.get("task_graph_sha256"):
        runtime.initialize_task_graph(occurred_at=started_at, actor_kind="SYSTEM", actor_id="agent-harness/task-graph")
    return runtime


def _start(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_root
    actor_id = _actor(args.run_id, args.actor_id)
    runtime = _ensure_graph(project, args.started_at, actor_id)
    task_id = runtime.next_task(occurred_at=args.started_at)
    if task_id is None:
        raise DiagnosticError(__import__("tools.lib.diagnostics", fromlist=["Finding"]).Finding("AGENT_NO_ELIGIBLE_TASK", "no runtime task is eligible for this agent run", file=str(project / "08_runtime/production-state.json"), remediation="Resolve the runtime dependency, resource, approval, or blocker condition before starting an agent run."))
    grant_max_seconds = int(load_config(REPOSITORY, "agent-harness-policy.yaml")["grant_max_seconds"])
    state = runtime.claim_task(task_id=task_id, occurred_at=args.started_at, actor_id=actor_id, lease_token=_token(args.run_id, task_id, 1), expires_at=_at(args.started_at, grant_max_seconds), idempotency_key=f"agent-harness/{args.run_id}/claim/{task_id}")
    attempt = int(state["task_states"][task_id]["attempt"])
    lease_token = _token(args.run_id, task_id, attempt)
    context, grant = build_context(project, REPOSITORY, task_id=task_id, lease_token=lease_token, run_id=args.run_id, generated_at=args.started_at, actor_kind="AGENT", actor_id=actor_id)
    run = create_run(project, REPOSITORY, run_id=args.run_id, adapter_profile_id=args.adapter_profile, target_state=args.target_state, started_at=args.started_at, actor_id=actor_id, task_ref={"id": task_id, "revision": 1, "sha256": context["task"]["sha256"]}, lease_token=lease_token, context=context, grant=grant)
    return {"run": run, "task_id": task_id, "context_sha256": context["integrity"]["content_sha256"], "grant_sha256": grant["integrity"]["content_sha256"]}


def _step(args: argparse.Namespace) -> dict[str, Any]:
    store = HarnessStore(args.project_root, REPOSITORY)
    run = store.read_run(args.run_id)
    if run["status"] != "RUNNING":
        raise DiagnosticError(__import__("tools.lib.diagnostics", fromlist=["Finding"]).Finding("AGENT_RUN_STATUS", f"agent run is {run['status']} and cannot execute a step", file=str(store.path(f"runs/{args.run_id}.json")), remediation="Resume only a RUNNING run, or create a new run from the current task lease."))
    state = store.replay(args.run_id)
    limits = run["stopping_policy"]
    if state["step"] >= limits["max_steps"] or state["invocation_count"] >= limits["max_invocations"]:
        _sync_run(args.project_root, args.run_id, status="BLOCKED", reason="configured agent stopping limit reached", updated_at=args.occurred_at)
        raise DiagnosticError(__import__("tools.lib.diagnostics", fromlist=["Finding"]).Finding("AGENT_STOPPING_LIMIT", "agent stopping limit was reached before another step", file=str(store.path(f"runs/{args.run_id}.json")), remediation="Inspect the bounded run and create an explicit follow-up run after review."))
    task_id = run["task_ref"]["id"]
    runtime_state = Runtime(args.project_root, REPOSITORY).replay()
    task_state = runtime_state.get("task_states", {}).get(task_id) or {}
    lease = task_state.get("lease") or {}
    attempt = int(task_state.get("attempt", 1))
    lease_token = _token(args.run_id, task_id, attempt)
    if task_state.get("status") != "RUNNING" or lease.get("lease_token") != lease_token:
        raise DiagnosticError(__import__("tools.lib.diagnostics", fromlist=["Finding"]).Finding("AGENT_LEASE_STALE", "the runtime lease is no longer owned by this run", file=str(args.project_root / "08_runtime/production-state.json"), remediation="Stop and reconcile the stale lease before creating another worker invocation."))
    store.append(args.run_id, "STEP_STARTED", {"step": state["step"] + 1, "task_id": task_id}, occurred_at=args.occurred_at, actor_kind="AGENT", actor_id=run["actor"]["id"], idempotency_key=f"{args.run_id}/step/{state['step'] + 1}")
    state = store.replay(args.run_id)
    invocation_id = f"AIN{state['invocation_count'] + 1:06d}"
    invocation, actions = run_invocation(args.project_root, REPOSITORY, run_id=args.run_id, task_id=task_id, lease_token=lease_token, context_sha256=run["context_sha256"], adapter_profile_id=run["adapter_profile_id"], invocation_id=invocation_id, started_at=args.occurred_at)
    applied = apply_actions(args.project_root, REPOSITORY, run_id=args.run_id, invocation_id=invocation_id, lease_token=lease_token, actions=actions, applied_at=args.occurred_at, actor_id=run["actor"]["id"])
    if applied["status"] == "WAITING_APPROVAL":
        updated = _sync_run(args.project_root, args.run_id, status="WAITING_APPROVAL", reason="worker requested a human-approved effect", updated_at=args.occurred_at)
        return {"run": updated, "invocation": invocation, "applied": applied, "runtime_task_status": task_state["status"]}
    stop_requested = any(action.get("kind") == "STOP" for action in actions)
    updated = _sync_run(args.project_root, args.run_id, status="BLOCKED" if stop_requested else "COMPLETE", reason="worker proposal recorded; canonical task completion remains explicit" if not stop_requested else "worker requested stop", updated_at=args.occurred_at)
    runtime_task_status = Runtime(args.project_root, REPOSITORY).replay()["task_states"][task_id]["status"]
    return {"run": updated, "invocation": invocation, "applied": applied, "runtime_task_status": runtime_task_status}


def _status(args: argparse.Namespace) -> dict[str, Any]:
    store = HarnessStore(args.project_root, REPOSITORY)
    run = store.read_run(args.run_id)
    state = store.replay(args.run_id)
    return {"run": run, "state": state}


def _cancel(args: argparse.Namespace) -> dict[str, Any]:
    if not args.approval_ref:
        raise DiagnosticError(__import__("tools.lib.diagnostics", fromlist=["Finding"]).Finding("AGENT_CANCEL_APPROVAL", "cancellation requires an explicit human approval reference", file=str(args.project_root), remediation="Supply --approval-ref for the exact run cancellation decision."))
    run = update_run(args.project_root, REPOSITORY, args.run_id, status="CANCELLED", occurred_at=args.occurred_at, actor_kind="HUMAN", actor_id=args.actor_id or "human/cancellation", reason=f"human approval {args.approval_ref}")
    return {"run": _sync_run(args.project_root, args.run_id, status="CANCELLED", updated_at=args.occurred_at, reason=f"human approval {args.approval_ref}")}


def _resume(args: argparse.Namespace) -> dict[str, Any]:
    store = HarnessStore(args.project_root, REPOSITORY)
    run = store.read_run(args.run_id)
    if run["status"] != "WAITING_APPROVAL":
        return _step(args)
    if not args.approval_ref:
        raise DiagnosticError(__import__("tools.lib.diagnostics", fromlist=["Finding"]).Finding("AGENT_APPROVAL_REQUIRED", "waiting agent run requires an explicit approval reference before resume", file=str(store.path(f"runs/{args.run_id}.json")), remediation="Record and validate the exact HUMAN approval in the runtime approval register, then pass its approval ID to resume."))
    runtime = Runtime(args.project_root, REPOSITORY)
    runtime_state = runtime.replay()
    plan, task = _load_plan_task(args.project_root, str(run["task_ref"]["id"]))
    requirement_ids = [str(item) for item in task.get("approval_requirement_ids", [])]
    try:
        valid_ids = runtime._valid_approval_ids(runtime_state, requirement_ids, occurred_at=args.occurred_at, plan=plan)
    except DiagnosticError as exc:
        raise DiagnosticError(Finding("AGENT_APPROVAL_INVALID", exc.finding.reason, file=exc.finding.file, location=exc.finding.location, remediation="Use a current HUMAN approval with exact action, target hash, authority, and expiry.")) from exc
    if args.approval_ref not in valid_ids:
        raise DiagnosticError(__import__("tools.lib.diagnostics", fromlist=["Finding"]).Finding("AGENT_APPROVAL_INVALID", "approval reference is not an approved runtime record", file=str(args.project_root / "08_runtime/production-state.json"), remediation="Use a current HUMAN approval with exact action, target hash, authority, and expiry."))
    update_run(args.project_root, REPOSITORY, args.run_id, status="RUNNING", occurred_at=args.occurred_at, actor_id=run["actor"]["id"], reason=f"resumed with approved reference {args.approval_ref}")
    return _step(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--project-root", type=Path, required=True)
    start.add_argument("--run-id", required=True)
    start.add_argument("--adapter-profile", default="scripted-fake")
    start.add_argument("--target-state", choices=("COMPLETE", "COMPLETE_WITH_GAPS", "BLOCKED", "FAILED", "CANCELLED"), default="COMPLETE")
    start.add_argument("--started-at", required=True)
    start.add_argument("--actor-id")

    for name in ("step", "run", "resume"):
        item = subparsers.add_parser(name)
        item.add_argument("--project-root", type=Path, required=True)
        item.add_argument("--run-id", required=True)
        item.add_argument("--occurred-at", required=True)
        if name == "resume":
            item.add_argument("--approval-ref")

    status = subparsers.add_parser("status")
    status.add_argument("--project-root", type=Path, required=True)
    status.add_argument("--run-id", required=True)

    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("--project-root", type=Path, required=True)
    cancel.add_argument("--run-id", required=True)
    cancel.add_argument("--occurred-at", required=True)
    cancel.add_argument("--approval-ref", required=True)
    cancel.add_argument("--actor-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            result = _start(args)
        elif args.command in {"step", "run", "resume"}:
            if args.command == "run":
                store = HarnessStore(args.project_root, REPOSITORY)
                if not store.path(f"runs/{args.run_id}.json").is_file():
                    raise DiagnosticError(__import__("tools.lib.diagnostics", fromlist=["Finding"]).Finding("AGENT_RUN_NOT_STARTED", "run requires start to bind an explicit timestamp and task lease", file=str(store.root), remediation="Run the start command once, then invoke run or step."))
            result = _resume(args) if args.command == "resume" else _step(args)
        elif args.command == "status":
            result = _status(args)
        else:
            result = _cancel(args)
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format="json")
        if exc.finding.rule in {"AGENT_CANCEL_APPROVAL", "AGENT_CAPABILITY_DENIED", "AGENT_TOOL_UNKNOWN"}:
            return EXIT_APPROVAL_REQUIRED
        if exc.finding.rule in {"AGENT_NO_ELIGIBLE_TASK", "AGENT_STOPPING_LIMIT"}:
            return EXIT_EXTERNAL_BLOCKED
        return EXIT_VALIDATION
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return EXIT_VALIDATION
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
