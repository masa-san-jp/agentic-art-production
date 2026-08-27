"""Provider-neutral, single-writer agent harness primitives.

The harness deliberately keeps worker data separate from production canonical
records.  Workers receive a task-scoped context over stdin and return action
proposals; only the broker and existing runtime APIs may change canonical
records.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .canonical import canonical_sha256, event_sha256, sha256_bytes
from .config import load_config
from .diagnostics import DiagnosticError, Finding
from .evidence import EvidenceManager
from .runtime import Runtime
from .schema import load_schema, validate_instance
from .yaml_io import load_json, load_yaml


HARNESS_RELATIVE = Path("08_runtime/agent-harness")
PROTOCOL_VERSION = "1.0.0"
EVENT_LOG = Path("agent-run-log.jsonl")
STATE_FILE = Path("agent-run-state.json")


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str, context: dict[str, Any] | None = None) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation, context=context or {})


def _error(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str, context: dict[str, Any] | None = None) -> DiagnosticError:
    return DiagnosticError(_finding(rule, reason, file=file, location=location, remediation=remediation, context=context))


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset")
    return parsed


def _format_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _numeric_id(prefix: str, value: str) -> str:
    suffix = "".join(char for char in value if char.isdigit())
    number = int(suffix or "1")
    return f"{prefix}{number:06d}"


def _hash_token(token: str) -> str:
    return sha256_bytes(token.encode("utf-8"))


def _schema_hash(repository: Path, filename: str) -> str:
    return sha256_bytes((repository / "schemas" / filename).read_bytes())


def _policy(repository: Path) -> dict[str, Any]:
    value = load_config(repository, "agent-harness-policy.yaml")
    if not isinstance(value, dict):
        raise _error("AGENT_POLICY", "agent-harness-policy.yaml must be a mapping", file=repository / "config/agent-harness-policy.yaml", remediation="Restore the versioned harness policy mapping.")
    return value


def _profile(repository: Path, profile_id: str) -> dict[str, Any]:
    policy = _policy(repository)
    profiles = [item for item in policy.get("profiles", []) if isinstance(item, dict) and item.get("id") == profile_id]
    if len(profiles) != 1:
        raise _error("AGENT_ADAPTER_PROFILE", f"adapter profile {profile_id!r} is not registered exactly once", file=repository / "config/agent-harness-policy.yaml", remediation="Use one registered adapter profile with an explicit protocol and isolation mode.")
    profile = profiles[0]
    if profile.get("adapter_protocol_version") != PROTOCOL_VERSION:
        raise _error("AGENT_PROTOCOL_VERSION", "adapter protocol version does not match the harness protocol", file=repository / "config/agent-harness-policy.yaml", remediation="Use a profile that declares the supported protocol version.")
    if profile.get("isolation") == "UNSANDBOXED":
        raise _error("AGENT_ADAPTER_ISOLATION", "unsandboxed adapters are not permitted", file=repository / "config/agent-harness-policy.yaml", remediation="Use the scripted fake or an attested sandbox profile.")
    return profile


def _scan_security(value: Any, *, repository: Path, file: Path | str, location: str = "/") -> None:
    safety = load_config(repository, "safety-policy.yaml")
    forbidden = [str(item) for item in safety.get("forbidden_markers", [])]
    signed = [str(item) for item in safety.get("signed_url_markers", [])]
    secret_names = {str(item).upper() for item in _policy(repository).get("secret_env_names", [])}

    def walk(item: Any, pointer: str) -> None:
        if isinstance(item, str):
            if any(marker in item for marker in forbidden + signed) or re.search(r"(?i)(credential|api[_-]?key|password|secret|private[_-]?raw|restricted)", item):
                raise _error("AGENT_PRIVATE_DATA", "worker data contains a prohibited secret, private classification, or signed URL marker", file=file, location=pointer, remediation="Pass only metadata hashes and opaque references; never pass credentials or private source bodies.")
            if item.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:[\\/]", item):
                raise _error("AGENT_UNSAFE_PATH", "worker data contains a local absolute path", file=file, location=pointer, remediation="Use a task-scoped logical scope or opaque external reference instead of a machine path.")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).upper() in secret_names or str(key).lower() in {"lease_token", "credential", "credentials", "environment"}:
                    raise _error("AGENT_PRIVATE_DATA", "worker data contains a raw secret-bearing field", file=file, location=f"{pointer.rstrip('/')}/{key}", remediation="Store only a hash or an allowlisted non-secret metadata field.")
                walk(child, f"{pointer.rstrip('/')}/{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f"{pointer.rstrip('/')}/{index}")

    walk(value, location)


def _schema_validate(value: dict[str, Any], repository: Path, filename: str) -> None:
    schema_path = repository / "schemas" / filename
    schema = load_schema(schema_path)
    common = load_schema(repository / "schemas/common.schema.json")
    findings = validate_instance(value, schema, schema_path=schema_path, common_schema=common)
    if findings:
        raise DiagnosticError(findings[0])


def _integrity(value: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(value)
    value["integrity"] = {"content_sha256": canonical_sha256({key: item for key, item in value.items() if key != "integrity"})}
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class HarnessStore:
    def __init__(self, project: Path, repository: Path):
        self.project = project.resolve()
        self.repository = repository.resolve()
        self.root = self.project / HARNESS_RELATIVE
        self.lock_path = self.root / ".harness.lock"

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def path(self, relative: str | Path) -> Path:
        return self.root / Path(relative)

    def context_path(self, content_hash: str) -> Path:
        return self.path("contexts") / f"{content_hash.replace(':', '-')}.json"

    def read_run(self, run_id: str) -> dict[str, Any]:
        path = self.path(f"runs/{run_id}.json")
        if not path.is_file():
            raise _error("AGENT_RUN_MISSING", f"agent run {run_id!r} is missing", file=path, remediation="Start the run through run_agent_harness.py before requesting a step.")
        value = load_json(path)
        if not isinstance(value, dict):
            raise _error("AGENT_RUN_OBJECT", "agent run record must be an object", file=path, remediation="Regenerate the run through the canonical harness writer.")
        _schema_validate(value, self.repository, "agent-run.schema.json")
        if value.get("integrity", {}).get("content_sha256") != canonical_sha256({key: item for key, item in value.items() if key != "integrity"}):
            raise _error("AGENT_RUN_INTEGRITY", "agent run content hash does not match its canonical record", file=path, remediation="Restore the run record from the append-only harness event log.")
        return value

    def read_context(self, content_hash: str) -> dict[str, Any]:
        path = self.context_path(content_hash)
        if not path.is_file():
            raise _error("AGENT_CONTEXT_MISSING", "requested context snapshot is missing", file=path, remediation="Regenerate a context from the current claimed task and lease.")
        value = load_json(path)
        if not isinstance(value, dict):
            raise _error("AGENT_CONTEXT_OBJECT", "agent context must be an object", file=path, remediation="Regenerate the context as canonical JSON.")
        _schema_validate(value, self.repository, "agent-context.schema.json")
        if value.get("integrity", {}).get("content_sha256") != content_hash:
            raise _error("AGENT_CONTEXT_STALE", "context filename/hash does not match its content", file=path, remediation="Use the context hash issued for the current baseline and lease.")
        _scan_security(value, repository=self.repository, file=path)
        return value

    def read_grant(self, grant_hash: str) -> dict[str, Any]:
        for path in sorted((self.path("grants")).glob("*.json")) if self.path("grants").is_dir() else []:
            value = load_json(path)
            if isinstance(value, dict) and value.get("integrity", {}).get("content_sha256") == grant_hash:
                _schema_validate(value, self.repository, "capability-grant.schema.json")
                _scan_security(value, repository=self.repository, file=path)
                return value
        raise _error("AGENT_CAPABILITY_STALE", "capability grant snapshot is missing or stale", file=self.path("grants"), remediation="Regenerate the grant from the current task, policy, and lease.")

    def _events(self) -> list[dict[str, Any]]:
        path = self.path(EVENT_LOG)
        if not path.is_file() or path.stat().st_size == 0:
            return []
        raw = path.read_bytes()
        if not raw.endswith(b"\n"):
            raise _error("AGENT_EVENT_PARTIAL_LINE", "agent run log does not end with a complete line", file=path, remediation="Restore the complete append-only event before resuming.")
        events: list[dict[str, Any]] = []
        previous: str | None = None
        for sequence, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise _error("AGENT_EVENT_JSON", str(exc), file=path, location=f"/events/{sequence}", remediation="Restore one canonical JSON event per line.") from exc
            if not isinstance(event, dict) or event.get("sequence") != sequence or event.get("previous_event_sha256") != previous or event_sha256(event) != event.get("event_sha256"):
                raise _error("AGENT_EVENT_HASH", "agent run log sequence or hash chain is invalid", file=path, location=f"/events/{sequence}", remediation="Restore the original event chain; automatic renumbering is forbidden.")
            _schema_validate(event, self.repository, "agent-run-event.schema.json")
            events.append(event)
            previous = event["event_sha256"]
        return events

    def replay(self, run_id: str | None = None, *, check_projection: bool = True) -> dict[str, Any]:
        events = self._events()
        if not events:
            raise _error("AGENT_RUN_LOG_EMPTY", "agent run log is empty", file=self.path(EVENT_LOG), remediation="Start an agent run before replaying its state.")
        created = next((event for event in events if event.get("type") == "RUN_CREATED"), None)
        if not isinstance(created, dict):
            raise _error("AGENT_RUN_CREATE_MISSING", "agent run log has no RUN_CREATED event", file=self.path(EVENT_LOG), remediation="Create the run through the canonical harness API.")
        payload = created.get("payload") or {}
        expected_run = run_id or payload.get("run_id")
        state: dict[str, Any] = {"schema_version": "1.0.0", "run_id": expected_run, "project_id": payload.get("project_id"), "status": "PENDING", "step": 0, "invocation_count": 0, "action_count": 0, "input_bytes": 0, "output_bytes": 0, "wall_seconds": 0, "last_event_id": None, "last_event_sha256": None}
        for event in events:
            if event.get("payload", {}).get("run_id", expected_run) != expected_run:
                raise _error("AGENT_RUN_CHAIN", "event references a different run", file=self.path(EVENT_LOG), remediation="Use one append-only log per agent run.")
            event_payload = event.get("payload") or {}
            if event["type"] == "RUN_CREATED":
                state["status"] = event_payload.get("status", "PENDING")
            elif event["type"] == "RUN_STATUS_CHANGED":
                state["status"] = event_payload["status"]
                if event_payload.get("reason"):
                    state["reason"] = event_payload["reason"]
            elif event["type"] == "STEP_STARTED":
                state["step"] += 1
            elif event["type"] == "INVOCATION_RECORDED":
                state["invocation_count"] += 1
                state["input_bytes"] += int(event_payload.get("input_bytes", 0))
                state["output_bytes"] += int(event_payload.get("output_bytes", 0))
                state["wall_seconds"] += int(event_payload.get("wall_seconds", 0))
            elif event["type"] == "ACTION_RECORDED":
                state["action_count"] += int(event_payload.get("count", 1))
            elif event["type"] == "TOOL_RESULT_RECORDED":
                pass
            state["last_event_id"] = event["event_id"]
            state["last_event_sha256"] = event["event_sha256"]
        state = _integrity(state)
        actual_path = self.path(STATE_FILE)
        if check_projection and actual_path.is_file():
            actual = load_json(actual_path)
            if actual != state:
                raise _error("AGENT_STATE_DIVERGENCE", "agent run state projection does not match replay", file=actual_path, remediation="Restore the projection from the append-only harness log; do not edit it in place.")
        return state

    def append(self, run_id: str, event_type: str, payload: dict[str, Any], *, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str) -> dict[str, Any]:
        with self.lock():
            events = self._events() if self.path(EVENT_LOG).exists() else []
            for existing in events:
                if existing.get("idempotency_key") == idempotency_key:
                    if existing.get("type") == event_type and existing.get("payload") == payload:
                        return self.replay(run_id)
                    raise _error("AGENT_EVENT_IDEMPOTENCY_MISMATCH", "idempotency key was already used with different agent event content", file=self.path(EVENT_LOG), remediation="Reuse the original event content or choose a new idempotency key.")
            event = {"event_id": f"AHE{len(events) + 1:06d}", "sequence": len(events) + 1, "occurred_at": occurred_at, "type": event_type, "actor": {"kind": actor_kind, "id": actor_id}, "idempotency_key": idempotency_key, "previous_event_sha256": events[-1]["event_sha256"] if events else None, "payload": {"run_id": run_id, **payload}}
            event["event_sha256"] = event_sha256(event)
            _schema_validate(event, self.repository, "agent-run-event.schema.json")
            self.path(EVENT_LOG).parent.mkdir(parents=True, exist_ok=True)
            with self.path(EVENT_LOG).open("ab") as output:
                output.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
                output.flush()
                os.fsync(output.fileno())
            state = self.replay(run_id, check_projection=False)
            _atomic_json(self.path(STATE_FILE), state)
            run_path = self.path(f"runs/{run_id}.json")
            if run_path.is_file():
                run = load_json(run_path)
                if isinstance(run, dict):
                    run["status"] = state["status"]
                    run["last_event_id"] = state["last_event_id"]
                    run["last_event_sha256"] = state["last_event_sha256"]
                    run["usage"] = {"invocation_count": state["invocation_count"], "step_count": state["step"], "wall_seconds": state["wall_seconds"], "input_bytes": state["input_bytes"], "output_bytes": state["output_bytes"]}
                    run["updated_at"] = occurred_at
                    _atomic_json(run_path, _integrity(run))
            return state


def _task_hash(task: dict[str, Any]) -> str:
    return canonical_sha256(task)


def _record_ref(kind: str, record: dict[str, Any], *, identifier: str = "id") -> dict[str, Any]:
    revision = record.get("revision", record.get("plan_revision", 1))
    return {"kind": kind, "id": str(record[identifier]), "revision": int(revision), "sha256": canonical_sha256(record)}


def _load_plan_task(project: Path, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_path = project / "03_plan/production-plan.yaml"
    plan = load_yaml(plan_path)
    if not isinstance(plan, dict):
        raise _error("AGENT_TASK_PLAN", "production plan must be an object", file=plan_path, remediation="Build and validate the current production plan before creating an agent context.")
    task = next((item for item in plan.get("tasks", []) if isinstance(item, dict) and item.get("id") == task_id), None)
    if not isinstance(task, dict):
        raise _error("AGENT_TASK_UNKNOWN", f"task {task_id!r} is not present in the current plan", file=plan_path, remediation="Use an ID from the current runtime task graph.")
    return plan, task


def build_context(project: Path, repository: Path, *, task_id: str, lease_token: str, run_id: str, generated_at: str, actor_kind: str, actor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if actor_kind not in {"AGENT", "SYSTEM"}:
        raise _error("AGENT_CONTEXT_AUTHORITY", "context generation accepts only AGENT or SYSTEM actors", file=project, remediation="Use the worker or a system process to build task context.")
    runtime = Runtime(project, repository)
    state = runtime.replay()
    task_state = state.get("task_states", {}).get(task_id)
    if not isinstance(task_state, dict) or task_state.get("status") != "RUNNING":
        raise _error("AGENT_LEASE_STATE", "context requires a currently RUNNING task lease", file=project / "08_runtime/production-state.json", remediation="Claim the task through Runtime before generating a worker context.")
    lease = task_state.get("lease") or {}
    if lease.get("owner") != actor_id or lease.get("lease_token") != lease_token:
        raise _error("AGENT_LEASE_MISMATCH", "context lease token or owner does not match the current runtime lease", file=project / "08_runtime/production-state.json", remediation="Use the exact lease issued by the runtime; raw token is never persisted in context.")
    plan, task = _load_plan_task(project, task_id)
    task_ref = {"id": task_id, "revision": 1, "sha256": _task_hash(task)}
    dependencies: list[dict[str, Any]] = []
    task_by_id = {str(item.get("id")): item for item in plan.get("tasks", []) if isinstance(item, dict)}
    for dependency in sorted(str(item) for item in task.get("depends_on", [])):
        if dependency in task_by_id:
            dependencies.append(_record_ref("TASK", task_by_id[dependency]))
    refs: list[dict[str, Any]] = [_record_ref("PLAN", plan, identifier="plan_id")]
    for collection, kind in (("deliverables", "DELIVERABLE"), ("technical_specifications", "SPECIFICATION"), ("acceptance_tests", "ACCEPTANCE_TEST"), ("resources", "RESOURCE"), ("materials", "MATERIAL"), ("risks", "RISK"), ("gaps", "GAP")):
        for record in plan.get(collection, []):
            if not isinstance(record, dict):
                continue
            ids = set(str(item) for item in task.get("trace_refs", []))
            if str(record.get("id")) in ids or collection in {"resources", "materials"} and str(record.get("id")) in set(task.get("required_resource_ids", []) + task.get("required_material_ids", [])):
                refs.append(_record_ref(kind, record))
    handoff = load_yaml(project / "00_handoff/production-handoff.yaml")
    prohibited = list((handoff.get("constraints") or {}).get("prohibited_actions", [])) if isinstance(handoff, dict) else []
    policy = _policy(repository)
    limits = {key: int(policy[key]) for key in ("max_invocations", "max_steps", "max_wall_seconds", "max_input_bytes", "max_output_bytes", "max_action_count")}
    grant_id = _numeric_id("ACG", run_id)
    effect_type = str(task.get("effect_type"))
    autonomous_effects = {str(item) for item in policy.get("autonomous_effect_types", [])}
    request_only_effects = {str(item) for item in policy.get("request_only_effect_types", [])}
    if effect_type not in autonomous_effects and effect_type not in request_only_effects:
        raise _error("AGENT_POLICY_EFFECT", f"task effect type {effect_type!r} is not classified by the harness policy", file=repository / "config/agent-harness-policy.yaml", remediation="Classify the effect type as autonomous or request-only before issuing a capability grant.")
    target_hashes = [task_ref["sha256"]]
    approval_requirements = (plan.get("approval_register") or {}).get("requirements", []) if isinstance(plan.get("approval_register"), dict) else []
    for requirement in approval_requirements:
        if isinstance(requirement, dict) and task_id in [str(item) for item in requirement.get("task_ids", [])] and isinstance(requirement.get("target_sha256"), str):
            target_hashes.append(requirement["target_sha256"])
    grant = {"schema_version": "1.0.0", "grant_id": grant_id, "run_id": run_id, "task_ref": task_ref, "allowed_actions": list(policy.get("autonomous_action_kinds", [])), "allowed_tools": list(policy.get("allowed_tool_ids", [])), "allowed_effect_types": [], "read_scopes": ["current-plan", "claimed-task", "direct-dependencies"], "write_scopes": [], "target_hashes": sorted(set(target_hashes)), "expires_at": str(lease["expires_at"]), "policy_version": int(policy.get("version", 1)), "policy_sha256": canonical_sha256(policy)}
    if effect_type not in autonomous_effects:
        grant["allowed_actions"] = list(policy.get("approval_action_kinds", []))
        grant["allowed_effect_types"] = [effect_type]
    grant = _integrity(grant)
    _schema_validate(grant, repository, "capability-grant.schema.json")
    context_id = _numeric_id("CTX", run_id)
    context = {"schema_version": "1.0.0", "protocol_version": PROTOCOL_VERSION, "context_id": context_id, "project_id": runtime._project_id(), "run_id": run_id, "task": {**task_ref, "title": task["title"], "effect_type": task["effect_type"], "acceptance_condition": task["acceptance_condition"], "status": task_state["status"], "lease_token_sha256": _hash_token(lease_token), "lease_expires_at": lease["expires_at"]}, "dependencies": dependencies, "records": refs, "prohibited_actions": sorted(set(str(item) for item in prohibited)), "required_output_schemas": [{"id": "agent-action-envelope.v1", "version": "1.0.0", "sha256": _schema_hash(repository, "agent-action-envelope.schema.json")}], "approval_refs": [], "evidence_refs": [], "stopping_limits": limits}
    _scan_security(context, repository=repository, file=project / HARNESS_RELATIVE / "contexts")
    context = _integrity(context)
    _schema_validate(context, repository, "agent-context.schema.json")
    context_hash = context["integrity"]["content_sha256"]
    grant_hash = grant["integrity"]["content_sha256"]
    store = HarnessStore(project, repository)
    store.root.mkdir(parents=True, exist_ok=True)
    context_path = store.context_path(context_hash)
    grant_path = store.path("grants") / f"{grant_id}.json"
    for path, value in ((context_path, context), (grant_path, grant)):
        if path.is_file():
            existing = load_json(path)
            if existing != value:
                raise _error("AGENT_CONTEXT_IDEMPOTENCY_MISMATCH", "context or grant identity already exists with different content", file=path, remediation="Use a new run or restore the exact original context.")
        else:
            _atomic_json(path, value)
    return context, grant


def create_run(project: Path, repository: Path, *, run_id: str, adapter_profile_id: str, target_state: str, started_at: str, actor_id: str, task_ref: dict[str, Any], lease_token: str, context: dict[str, Any], grant: dict[str, Any]) -> dict[str, Any]:
    if not re.fullmatch(r"ARN[0-9]{6,}", run_id):
        raise _error("AGENT_RUN_ID", "run_id must use ARN###### format", file=project, remediation="Provide a stable run identity.")
    profile = _profile(repository, adapter_profile_id)
    policy = _policy(repository)
    stopping = {key: int(policy[key]) for key in ("max_invocations", "max_steps", "max_wall_seconds", "max_input_bytes", "max_output_bytes", "max_action_count")}
    run = {"schema_version": "1.0.0", "run_id": run_id, "project_id": Runtime(project, repository)._project_id(), "task_ref": task_ref, "target_state": target_state, "lease_token_sha256": _hash_token(lease_token), "adapter_profile_id": adapter_profile_id, "adapter_profile_version": str(profile.get("version")), "context_sha256": context["integrity"]["content_sha256"], "capability_grant_sha256": grant["integrity"]["content_sha256"], "stopping_policy": stopping, "usage": {"invocation_count": 0, "step_count": 0, "wall_seconds": 0, "input_bytes": 0, "output_bytes": 0}, "created_at": started_at, "updated_at": started_at, "actor": {"kind": "AGENT", "id": actor_id}, "status": "RUNNING", "last_event_id": None, "last_event_sha256": None}
    run = _integrity(run)
    _schema_validate(run, repository, "agent-run.schema.json")
    store = HarnessStore(project, repository)
    path = store.path(f"runs/{run_id}.json")
    if path.exists():
        existing = store.read_run(run_id)
        if existing == run:
            return existing
        raise _error("AGENT_RUN_IDEMPOTENCY_MISMATCH", "run ID already exists with different content", file=path, remediation="Reuse the original run parameters or choose another run ID.")
    _atomic_json(path, run)
    state = store.append(run_id, "RUN_CREATED", {"project_id": run["project_id"], "status": "RUNNING", "target_state": target_state}, occurred_at=started_at, actor_kind="AGENT", actor_id=actor_id, idempotency_key=f"{run_id}/create")
    run["last_event_id"] = state["last_event_id"]
    run["last_event_sha256"] = state["last_event_sha256"]
    run = _integrity(run)
    _atomic_json(path, run)
    return run


def update_run(project: Path, repository: Path, run_id: str, *, status: str, occurred_at: str, actor_id: str, actor_kind: str = "AGENT", reason: str | None = None) -> dict[str, Any]:
    store = HarnessStore(project, repository)
    run = store.read_run(run_id)
    run["status"] = status
    run["updated_at"] = occurred_at
    if reason:
        run["reason"] = reason
    run = _integrity(run)
    _schema_validate(run, repository, "agent-run.schema.json")
    _atomic_json(store.path(f"runs/{run_id}.json"), run)
    store.append(run_id, "RUN_STATUS_CHANGED", {"status": status, **({"reason": reason} if reason else {})}, occurred_at=occurred_at, actor_kind=actor_kind, actor_id=actor_id, idempotency_key=f"{run_id}/status/{status}/{occurred_at}")
    return run


def _scripted_worker_path(repository: Path) -> Path:
    profile = _profile(repository, "scripted-fake")
    path = repository / str(profile.get("worker_path", ""))
    if not path.is_file():
        raise _error("AGENT_ADAPTER_BINARY", "scripted worker fixture is missing", file=path, remediation="Restore the pinned offline worker fixture.")
    return path


def run_invocation(project: Path, repository: Path, *, run_id: str, task_id: str, lease_token: str, context_sha256: str, adapter_profile_id: str, invocation_id: str, started_at: str, worker_command: list[str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    store = HarnessStore(project, repository)
    run = store.read_run(run_id)
    context = store.read_context(context_sha256)
    grant = store.read_grant(run["capability_grant_sha256"])
    if context["run_id"] != run_id or context["task"]["id"] != task_id or context["task"]["lease_token_sha256"] != _hash_token(lease_token):
        raise _error("AGENT_CONTEXT_STALE", "context is not bound to the requested run, task, or lease", file=store.context_path(context_sha256), remediation="Regenerate a task-scoped context after claiming the current lease.")
    runtime_state = Runtime(project, repository).replay()
    task_state = runtime_state.get("task_states", {}).get(task_id, {})
    lease = task_state.get("lease") or {}
    if task_state.get("status") != "RUNNING" or lease.get("owner") != run["actor"]["id"] or lease.get("lease_token") != lease_token:
        raise _error("AGENT_LEASE_STALE", "worker invocation lease no longer belongs to the run", file=project / "08_runtime/production-state.json", remediation="Stop the invocation and reconcile the current runtime lease.")
    profile = _profile(repository, adapter_profile_id)
    if ("max_token_usage" in run["stopping_policy"] or "max_cost" in run["stopping_policy"]) and not profile.get("trusted_usage_meter"):
        raise _error("AGENT_USAGE_METER", "token or cost cap requires a trusted adapter usage meter", file=store.path(f"runs/{run_id}.json"), remediation="Use an attested adapter that supplies the configured usage counters.")
    request = {"protocol_version": PROTOCOL_VERSION, "run_id": run_id, "invocation_id": invocation_id, "task_ref": context["task"], "context_sha256": context_sha256, "capability_grant_sha256": run["capability_grant_sha256"], "deadline": lease["expires_at"]}
    _scan_security(request, repository=repository, file=store.path(f"invocations/{invocation_id}.json"))
    raw_request = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    policy = _policy(repository)
    if len(raw_request) > int(policy["max_input_bytes"]):
        raise _error("AGENT_INPUT_LIMIT", "worker request exceeds the configured input byte limit", file=store.path(f"invocations/{invocation_id}.json"), remediation="Reduce the task-scoped context to the minimum required records.")
    invocation_path = store.path(f"invocations/{invocation_id}.json")
    if invocation_path.is_file():
        existing = load_json(invocation_path)
        if isinstance(existing, dict) and existing.get("request_sha256") == sha256_bytes(raw_request):
            response_path = store.path(f"responses/{invocation_id}.json")
            actions = load_json(response_path).get("actions", []) if response_path.is_file() and isinstance(load_json(response_path), dict) else []
            return existing, actions if isinstance(actions, list) else []
        raise _error("AGENT_INVOCATION_IDEMPOTENCY_MISMATCH", "invocation ID already exists for a different request", file=invocation_path, remediation="Reuse the original invocation request or choose another invocation ID.")
    command = worker_command or [sys.executable, str(_scripted_worker_path(repository))]
    if not command or any(not isinstance(item, str) or not item for item in command) or command[0] in {"sh", "bash", "zsh", "shell"}:
        raise _error("AGENT_ADAPTER_COMMAND", "worker command must be a fixed executable argv and cannot invoke a shell", file=invocation_path, remediation="Use an explicit allowlisted executable and argv.")
    if command[0] != sys.executable or len(command) != 2 or Path(command[1]).resolve() != _scripted_worker_path(repository).resolve():
        raise _error("AGENT_ADAPTER_COMMAND", "only a repository-pinned fixture worker executable is allowed by the offline profile", file=invocation_path, remediation="Register an isolated adapter profile and pinned worker path before using a different adapter.")
    worker_dir = Path(tempfile.mkdtemp(prefix="agent-worker-"))
    started = _timestamp(started_at)
    status = "FAILED"
    actions: list[dict[str, Any]] = []
    response_hash: str | None = None
    error_code: str | None = None
    stderr_hash: str | None = None
    output_bytes = 0
    try:
        try:
            completed = subprocess.run(command, input=raw_request, capture_output=True, cwd=worker_dir, env={}, timeout=max(1, int((_timestamp(lease["expires_at"]) - started).total_seconds())), check=False)
        except subprocess.TimeoutExpired:
            error_code = "AGENT_WORKER_TIMEOUT"
            raise _error(error_code, "worker exceeded its bounded invocation timeout", file=invocation_path, remediation="Reconcile the invocation and lease; do not retry an unknown external effect automatically.")
        output_bytes = len(completed.stdout)
        if len(completed.stdout) > int(policy["max_output_bytes"]):
            error_code = "AGENT_OUTPUT_LIMIT"
            raise _error(error_code, "worker stdout exceeds the configured output byte limit", file=invocation_path, remediation="Return a smaller schema-valid action proposal.")
        stderr = completed.stderr
        stderr_hash = sha256_bytes(stderr)
        _scan_security(stderr.decode("utf-8", errors="replace"), repository=repository, file=invocation_path, location="/stderr")
        if completed.returncode != 0:
            error_code = "AGENT_WORKER_EXIT"
            raise _error(error_code, "worker exited unsuccessfully", file=invocation_path, remediation="Inspect the bounded invocation metadata and retry only when the request is known not to have started an effect.")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            error_code = "AGENT_WORKER_RESPONSE"
            raise _error(error_code, "worker stdout must contain exactly one JSON response", file=invocation_path, remediation="Return one UTF-8 JSON object without Markdown or diagnostic text.") from exc
        if not isinstance(response, dict) or set(response) != {"actions"} or not isinstance(response.get("actions"), list):
            error_code = "AGENT_ACTION_SCHEMA"
            raise _error(error_code, "worker response must be exactly an actions object", file=invocation_path, remediation="Return {actions:[schema-valid action envelopes]} only.")
        if len(response["actions"]) > int(policy["max_action_count"]):
            error_code = "AGENT_ACTION_LIMIT"
            raise _error(error_code, "worker action count exceeds the configured limit", file=invocation_path, remediation="Stop and split the work into bounded invocations.")
        for action in response["actions"]:
            if not isinstance(action, dict):
                raise _error("AGENT_ACTION_SCHEMA", "worker action must be an object", file=invocation_path, remediation="Return schema-valid action envelopes.")
            _schema_validate(action, repository, "agent-action-envelope.schema.json")
            _scan_security(action, repository=repository, file=invocation_path)
        actions = response["actions"]
        response_hash = canonical_sha256(response)
        status = "SUCCEEDED"
        response_path = store.path(f"responses/{invocation_id}.json")
        _atomic_json(response_path, response)
    finally:
        finished = _format_timestamp(started + timedelta(seconds=0))
        invocation = {"schema_version": "1.0.0", "invocation_id": invocation_id, "run_id": run_id, "task_ref": run["task_ref"], "context_sha256": context_sha256, "capability_grant_sha256": run["capability_grant_sha256"], "adapter_profile_id": adapter_profile_id, "adapter_profile_version": str(profile.get("version")), "protocol_version": PROTOCOL_VERSION, "request_sha256": sha256_bytes(raw_request), "response_sha256": response_hash, "status": status, "started_at": started_at, "finished_at": finished, "input_bytes": len(raw_request), "output_bytes": output_bytes, "stderr_sha256": stderr_hash, **({"error_code": error_code} if error_code else {})}
        invocation = _integrity(invocation)
        _schema_validate(invocation, repository, "worker-invocation.schema.json")
        _atomic_json(invocation_path, invocation)
        try:
            shutil.rmtree(worker_dir)
        except OSError:
            pass
    if status != "SUCCEEDED":
        raise _error(error_code or "AGENT_INVOCATION_FAILED", "worker invocation failed", file=invocation_path, remediation="Inspect invocation status and resume only from a safe checkpoint.")
    store.append(run_id, "INVOCATION_RECORDED", {"invocation_id": invocation_id, "status": status, "input_bytes": len(raw_request), "output_bytes": output_bytes, "wall_seconds": 0}, occurred_at=started_at, actor_kind="AGENT", actor_id=run["actor"]["id"], idempotency_key=f"{run_id}/invocation/{invocation_id}")
    return invocation, actions


def apply_actions(project: Path, repository: Path, *, run_id: str, invocation_id: str, lease_token: str, actions: list[dict[str, Any]], applied_at: str, actor_id: str) -> dict[str, Any]:
    store = HarnessStore(project, repository)
    run = store.read_run(run_id)
    if run["status"] != "RUNNING":
        raise _error("AGENT_RUN_STATUS", f"agent run is {run['status']} and cannot accept worker actions", file=store.path(f"runs/{run_id}.json"), remediation="Apply actions only while the owning run is RUNNING.")
    context = store.read_context(run["context_sha256"])
    grant = store.read_grant(run["capability_grant_sha256"])
    if context["task"]["lease_token_sha256"] != _hash_token(lease_token):
        raise _error("AGENT_LEASE_STALE", "action context lease no longer matches the supplied lease", file=store.context_path(run["context_sha256"]), remediation="Discard stale worker output and use a newly issued context.")
    if len(actions) > int(run["stopping_policy"]["max_action_count"]):
        raise _error("AGENT_ACTION_LIMIT", "action batch exceeds the run stopping limit", file=store.path(f"runs/{run_id}.json"), remediation="Use one bounded action batch within the run limit.")
    invocation_path = store.path(f"invocations/{invocation_id}.json")
    if not invocation_path.is_file():
        raise _error("AGENT_INVOCATION_MISSING", "worker actions must reference invocation metadata recorded by the harness", file=invocation_path, remediation="Run the bounded worker invocation before submitting its action response.")
    invocation = load_json(invocation_path)
    if not isinstance(invocation, dict) or invocation.get("run_id") != run_id or invocation.get("invocation_id") != invocation_id or invocation.get("context_sha256") != run["context_sha256"] or invocation.get("capability_grant_sha256") != run["capability_grant_sha256"]:
        raise _error("AGENT_INVOCATION_STALE", "worker action invocation metadata does not match the current run", file=invocation_path, remediation="Discard stale output and submit actions from the current invocation only.")
    if len({action.get("action_id") for action in actions if isinstance(action, dict)}) != len(actions):
        raise _error("AGENT_ACTION_DUPLICATE", "an action batch must not contain duplicate action IDs", file=invocation_path, remediation="Assign one stable action ID to each proposal.")
    decisions: list[dict[str, Any]] = []
    for action in actions:
        _schema_validate(action, repository, "agent-action-envelope.schema.json")
        _scan_security(action, repository=repository, file=store.path(f"actions/{action['action_id']}.json"))
        if action["decision"] != "PROPOSED":
            raise _error("AGENT_ACTION_SCHEMA", "worker output must contain PROPOSED actions; authorization is broker or human state", file=store.path(f"actions/{action['action_id']}.json"), remediation="Return proposals with decision=PROPOSED and let the broker record the decision.")
        if action["run_id"] != run_id or action["invocation_id"] != invocation_id or action["context_sha256"] != run["context_sha256"] or action["capability_grant_sha256"] != run["capability_grant_sha256"] or action["task_ref"] != context["task"]:
            raise _error("AGENT_ACTION_STALE", "action identity or hash does not match the current run context", file=store.path(f"actions/{action['action_id']}.json"), remediation="Discard stale worker output and invoke the worker with the current context.")
        if action["kind"] not in grant["allowed_actions"]:
            raise _error("AGENT_CAPABILITY_DENIED", "action kind is outside the capability grant", file=store.path(f"actions/{action['action_id']}.json"), remediation="Request only an action explicitly derived from the task effect policy.")
        payload = action.get("payload") or {}
        if any(key in payload for key in ("state", "to_state", "from_state", "result", "production_result")):
            raise _error("AGENT_ACTION_SCHEMA", "worker cannot directly transition lifecycle state or complete a production result", file=store.path(f"actions/{action['action_id']}.json"), remediation="Return a proposal; use the canonical broker/runtime API for lifecycle changes.")
        if action["kind"] == "REQUEST_TOOL":
            tool_id = payload.get("tool_id")
            if tool_id not in grant["allowed_tools"]:
                raise _error("AGENT_TOOL_UNKNOWN", f"tool {tool_id!r} is not registered in the capability grant", file=store.path(f"actions/{action['action_id']}.json"), remediation="Use an allowlisted tool with a schema-valid input.")
            if not isinstance(payload.get("target_ref"), str) or not isinstance(payload.get("target_sha256"), str) or not isinstance(payload.get("input"), dict) or payload["target_sha256"] not in grant["target_hashes"]:
                raise _error("AGENT_TOOL_TARGET", "tool request requires an exact allowlisted target reference, target hash, and object input", file=store.path(f"actions/{action['action_id']}.json"), remediation="Bind the tool request to a current context target without passing a local path or raw source.")
            request_id = _numeric_id("ATR", action["action_id"])
            request = {"schema_version": "1.0.0", "tool_request_id": request_id, "run_id": run_id, "invocation_id": invocation_id, "tool_id": tool_id, "input": payload["input"], "target_ref": payload["target_ref"], "target_sha256": payload["target_sha256"], "idempotency_key": action["idempotency_key"]}
            _scan_security(request, repository=repository, file=store.path(f"tool-requests/{request_id}.json"))
            _schema_validate(request, repository, "tool-request.schema.json")
            request_path = store.path(f"tool-requests/{request_id}.json")
            if request_path.is_file() and load_json(request_path) != request:
                raise _error("AGENT_TOOL_IDEMPOTENCY", "tool request identity already exists with different content", file=request_path, remediation="Reuse the original tool request or choose a new action idempotency key.")
            if not request_path.is_file():
                _atomic_json(request_path, request)
        if action["kind"] in {"REQUEST_EFFECT", "REQUEST_APPROVAL"}:
            effect_type = payload.get("effect_type")
            if effect_type not in grant["allowed_effect_types"] or not isinstance(payload.get("target_ref"), str) or not isinstance(payload.get("target_sha256"), str) or payload["target_sha256"] not in grant["target_hashes"]:
                raise _error("AGENT_CAPABILITY_DENIED", "effect type is outside the capability grant", file=store.path(f"actions/{action['action_id']}.json"), remediation="Request an approval-mediated effect for the exact task effect type.")
            plan, task = _load_plan_task(project, str(context["task"]["id"]))
            requirements = (plan.get("approval_register") or {}).get("requirements", []) if isinstance(plan.get("approval_register"), dict) else []
            exact_requirements = [
                requirement for requirement in requirements
                if isinstance(requirement, dict)
                and str(requirement.get("id")) in {str(item) for item in task.get("approval_requirement_ids", [])}
                and requirement.get("action") == effect_type
                and requirement.get("target_ref") == payload.get("target_ref")
                and requirement.get("target_sha256") == payload.get("target_sha256")
            ]
            if len(exact_requirements) != 1:
                raise _error("AGENT_EFFECT_TARGET", "effect proposal does not match exactly one current approval requirement", file=store.path(f"actions/{action['action_id']}.json"), remediation="Use the current plan requirement's exact effect type, target reference, and target hash.")
        action_path = store.path(f"actions/{action['action_id']}.json")
        if action_path.is_file():
            existing = load_json(action_path)
            if existing != action:
                raise _error("AGENT_ACTION_IDEMPOTENCY_MISMATCH", "action ID already exists with different content", file=action_path, remediation="Reuse the original action envelope or choose a new idempotency key.")
        else:
            _atomic_json(action_path, action)
        decision = "WAITING_APPROVAL" if action["kind"] in {"REQUEST_APPROVAL", "REQUEST_EFFECT"} else "APPLIED"
        decisions.append({"action_id": action["action_id"], "decision": decision, "tool_id": payload.get("tool_id"), "effect_type": payload.get("effect_type")})
    store.append(run_id, "ACTION_RECORDED", {"invocation_id": invocation_id, "count": len(actions)}, occurred_at=applied_at, actor_kind="AGENT", actor_id=actor_id, idempotency_key=f"{run_id}/actions/{invocation_id}")
    return {"run_id": run_id, "invocation_id": invocation_id, "decisions": decisions, "status": "WAITING_APPROVAL" if any(item["decision"] == "WAITING_APPROVAL" for item in decisions) else "APPLIED"}


def record_tool_result(project: Path, repository: Path, *, run_id: str, tool_request_id: str, status: str, result_sha256: str, input_bytes: int, output_bytes: int, recorded_at: str, actor_id: str, error_code: str | None = None) -> dict[str, Any]:
    store = HarnessStore(project, repository)
    run = store.read_run(run_id)
    request_path = store.path(f"tool-requests/{tool_request_id}.json")
    if not request_path.is_file():
        raise _error("AGENT_TOOL_REQUEST_MISSING", "tool result references a missing tool request", file=request_path, remediation="Record a result only for a broker-issued tool request.")
    request = load_json(request_path)
    if not isinstance(request, dict) or request.get("run_id") != run_id:
        raise _error("AGENT_TOOL_REQUEST_RUN", "tool result request does not belong to the current run", file=request_path, remediation="Use the exact run-bound tool request identity.")
    actions = []
    for action_path in sorted((store.path("actions")).glob("*.json")) if store.path("actions").is_dir() else []:
        action = load_json(action_path)
        if isinstance(action, dict) and action.get("kind") == "REQUEST_TOOL" and _numeric_id("ATR", str(action.get("action_id"))) == tool_request_id:
            actions.append(action)
    if len(actions) != 1:
        raise _error("AGENT_TOOL_REQUEST_UNISSUED", "tool result does not reference exactly one broker-issued tool request action", file=request_path, remediation="Record a result only after the broker has persisted the matching REQUEST_TOOL action.")
    action = actions[0]
    payload = action.get("payload") or {}
    if any(request.get(key) != expected for key, expected in (("invocation_id", action.get("invocation_id")), ("tool_id", payload.get("tool_id")), ("input", payload.get("input")), ("target_ref", payload.get("target_ref")), ("target_sha256", payload.get("target_sha256")), ("idempotency_key", action.get("idempotency_key")))):
        raise _error("AGENT_TOOL_REQUEST_DIVERGENCE", "broker tool request does not match its action proposal", file=request_path, remediation="Restore the immutable request generated from the matching action envelope.")
    result = {"schema_version": "1.0.0", "tool_request_id": tool_request_id, "status": status, "result_sha256": result_sha256, "input_bytes": int(input_bytes), "output_bytes": int(output_bytes), "recorded_at": recorded_at, **({"error_code": error_code} if error_code else {})}
    result = _integrity(result)
    _schema_validate(result, repository, "tool-result.schema.json")
    _scan_security(result, repository=repository, file=store.path(f"tool-results/{tool_request_id}.json"))
    result_path = store.path(f"tool-results/{tool_request_id}.json")
    if result_path.is_file() and load_json(result_path) != result:
        raise _error("AGENT_TOOL_RESULT_IDEMPOTENCY", "tool result identity already exists with different content", file=result_path, remediation="Reuse the original result or record a new tool request.")
    if not result_path.is_file():
        _atomic_json(result_path, result)
    store.append(run_id, "TOOL_RESULT_RECORDED", {"tool_request_id": tool_request_id, "status": status, "input_bytes": int(input_bytes), "output_bytes": int(output_bytes)}, occurred_at=recorded_at, actor_kind="SYSTEM", actor_id=actor_id, idempotency_key=f"{run_id}/tool-result/{tool_request_id}")
    return result


def validate_harness_project(project: Path, repository: Path) -> list[Finding]:
    root = project / HARNESS_RELATIVE
    if not root.is_dir():
        return []
    try:
        store = HarnessStore(project, repository)
        state = store.replay()
        _schema_validate(state, repository, "agent-run-state.schema.json")
        runs: dict[str, dict[str, Any]] = {}
        for path in sorted((root / "runs").glob("*.json")) if (root / "runs").is_dir() else []:
            run = store.read_run(path.stem)
            runs[path.stem] = run
            if run.get("last_event_id") != state.get("last_event_id") or run.get("last_event_sha256") != state.get("last_event_sha256"):
                raise _error("AGENT_RUN_STATE_DIVERGENCE", "agent run record does not point at the current event projection", file=path, remediation="Refresh the run projection from agent-run-log.jsonl through the harness writer.")
        for path in sorted((root / "contexts").glob("*.json")) if (root / "contexts").is_dir() else []:
            value = load_json(path)
            if isinstance(value, dict):
                _schema_validate(value, repository, "agent-context.schema.json")
                _scan_security(value, repository=repository, file=path)
        for path in sorted((root / "grants").glob("*.json")) if (root / "grants").is_dir() else []:
            value = load_json(path)
            if isinstance(value, dict):
                _schema_validate(value, repository, "capability-grant.schema.json")
                _scan_security(value, repository=repository, file=path)
        for path in sorted((root / "invocations").glob("*.json")) if (root / "invocations").is_dir() else []:
            value = load_json(path)
            if not isinstance(value, dict):
                continue
            _schema_validate(value, repository, "worker-invocation.schema.json")
            if value.get("integrity", {}).get("content_sha256") != canonical_sha256({key: item for key, item in value.items() if key != "integrity"}):
                raise _error("AGENT_INVOCATION_INTEGRITY", "worker invocation metadata hash does not match its content", file=path, remediation="Restore immutable invocation metadata generated by the harness.")
            _scan_security(value, repository=repository, file=path)
            if value.get("run_id") not in runs:
                raise _error("AGENT_INVOCATION_RUN", "worker invocation references a missing run", file=path, remediation="Keep invocation metadata within its owning agent run.")
        for path in sorted((root / "actions").glob("*.json")) if (root / "actions").is_dir() else []:
            value = load_json(path)
            if isinstance(value, dict):
                _schema_validate(value, repository, "agent-action-envelope.schema.json")
                _scan_security(value, repository=repository, file=path)
        for path in sorted((root / "responses").glob("*.json")) if (root / "responses").is_dir() else []:
            value = load_json(path)
            if not isinstance(value, dict) or set(value) != {"actions"} or not isinstance(value["actions"], list):
                raise _error("AGENT_RESPONSE_SCHEMA", "worker response projection must contain exactly an actions array", file=path, remediation="Restore the response generated by the bounded worker protocol.")
            _scan_security(value, repository=repository, file=path)
            for action in value["actions"]:
                if not isinstance(action, dict):
                    raise _error("AGENT_RESPONSE_SCHEMA", "worker response action must be an object", file=path, remediation="Restore schema-valid action envelopes.")
                _schema_validate(action, repository, "agent-action-envelope.schema.json")
        for path in sorted((root / "tool-requests").glob("*.json")) if (root / "tool-requests").is_dir() else []:
            value = load_json(path)
            if isinstance(value, dict):
                _schema_validate(value, repository, "tool-request.schema.json")
                _scan_security(value, repository=repository, file=path)
        for path in sorted((root / "tool-results").glob("*.json")) if (root / "tool-results").is_dir() else []:
            value = load_json(path)
            if isinstance(value, dict):
                _schema_validate(value, repository, "tool-result.schema.json")
                if value.get("integrity", {}).get("content_sha256") != canonical_sha256({key: item for key, item in value.items() if key != "integrity"}):
                    raise _error("AGENT_TOOL_RESULT_INTEGRITY", "tool result receipt hash does not match its content", file=path, remediation="Restore the result receipt through the canonical tool-result writer.")
                _scan_security(value, repository=repository, file=path)
        return []
    except DiagnosticError as exc:
        return [exc.finding]
    except (OSError, TypeError, ValueError) as exc:
        return [_finding("AGENT_HARNESS_VALIDATION", str(exc), file=root, remediation="Restore the harness append-only log and projections, then replay.")]
