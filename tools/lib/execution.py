"""Traceable production outputs and installation results without asset bodies."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

import fcntl

from .canonical import canonical_sha256, event_sha256
from .config import load_config
from .diagnostics import DiagnosticError, Finding
from .evidence import resolve_evidence_refs
from .planning import validate_plan_document
from .schema import load_schema, validate_instance
from .security import check_text_security, validate_asset_uri
from .yaml_io import dump_yaml, load_jsonl, load_yaml


RECORD_SCHEMAS = {
    "OUTPUT_VERSION_RECORDED": ("output-version.schema.json", "output_id"),
    "QUALITY_RESULT_RECORDED": ("quality-result.schema.json", "quality_id"),
    "INSTALLATION_PLAN_RECORDED": ("installation-plan.schema.json", "installation_plan_id"),
    "INSTALLATION_RESULT_RECORDED": ("installation-result.schema.json", "installation_result_id"),
}
PROJECTION_FILES = {
    "OUTPUT_VERSION_RECORDED": ("05_execution/output-versions.yaml", "output-versions.schema.json"),
    "QUALITY_RESULT_RECORDED": ("05_execution/quality-results.yaml", "quality-results.schema.json"),
    "INSTALLATION_PLAN_RECORDED": ("06_installation/installation-plan.yaml", "installation-plan.schema.json"),
    "INSTALLATION_RESULT_RECORDED": ("06_installation/installation-results.yaml", "installation-results.schema.json"),
}
ALL_EVENT_TYPES = tuple(RECORD_SCHEMAS)


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _without_integrity(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "integrity"}


def _timestamp(value: str) -> None:
    from datetime import datetime

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset")


class ExecutionManager:
    """Single-writer event log and deterministic projections for one project."""

    def __init__(self, project_root: Path, repository: Path):
        self.project_root = project_root.resolve()
        self.repository = repository.resolve()
        self.execution_root = self.project_root / "05_execution"
        self.installation_root = self.project_root / "06_installation"
        self.log_path = self.execution_root / "production-log.jsonl"
        self.lock_path = self.execution_root / ".execution.lock"

    def _project_id(self) -> str:
        manifest = load_yaml(self.project_root / "manifest.yaml")
        if isinstance(manifest, dict) and isinstance(manifest.get("project_id"), str):
            return manifest["project_id"]
        raise DiagnosticError(_finding("EXECUTION_PROJECT_ID", "project_id is missing from manifest.yaml", file=self.project_root / "manifest.yaml", remediation="Restore a valid materialized production manifest."))

    def _common_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas/common.schema.json")

    def _schema_store(self) -> list[dict[str, Any]]:
        names = {
            "asset-reference.schema.json",
            "output-version.schema.json",
            "output-versions.schema.json",
            "quality-result.schema.json",
            "quality-results.schema.json",
            "installation-plan.schema.json",
            "installation-result.schema.json",
            "installation-results.schema.json",
            "evidence-record.schema.json",
            "evidence-register.schema.json",
        }
        return [load_schema(self.repository / "schemas" / name) for name in sorted(names)]

    def _event_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas/execution-event.schema.json")

    def _record_schema(self, event_type: str) -> dict[str, Any]:
        schema = load_schema(self.repository / "schemas" / RECORD_SCHEMAS[event_type][0])
        if event_type == "INSTALLATION_PLAN_RECORDED":
            return schema["$defs"]["record"]
        return schema

    def _projection_path(self, event_type: str) -> Path:
        return self.project_root / PROJECTION_FILES[event_type][0]

    def _projection_schema(self, event_type: str) -> dict[str, Any]:
        return load_schema(self.repository / "schemas" / PROJECTION_FILES[event_type][1])

    def _empty_projection(self) -> dict[str, Any]:
        value = {"schema_version": "1.0.0", "project_id": self._project_id(), "revision": 0, "records": []}
        value["integrity"] = {"content_sha256": canonical_sha256(value)}
        return value

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        self.execution_root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _write_yaml_atomic(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        try:
            os.close(fd)
            dump_yaml(value, Path(temporary))
            with Path(temporary).open("rb") as output:
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _append_event(self, event: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("ab") as output:
            output.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
            output.flush()
            os.fsync(output.fileno())

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.log_path.is_file() or self.log_path.stat().st_size == 0:
            return []
        if not self.log_path.read_bytes().endswith(b"\n"):
            raise DiagnosticError(_finding("EXECUTION_PARTIAL_LINE", "production-log.jsonl does not end with a complete newline-terminated event", file=self.log_path, remediation="Restore the complete final event; never truncate the audit log automatically."))
        common = self._common_schema()
        schema = self._event_schema()
        events: list[dict[str, Any]] = []
        try:
            records = load_jsonl(self.log_path)
        except DiagnosticError as exc:
            raise DiagnosticError(_finding("EXECUTION_LOG_SYNTAX", exc.finding.reason, file=self.log_path, remediation="Restore a valid JSON object on every log line.")) from exc
        for line_number, event in records:
            self._validate_event_schema(event, line_number=line_number, schema=schema, common=common)
            events.append(event)
        return events

    def _validate_event_schema(self, event: dict[str, Any], *, line_number: int | None = None, schema: dict[str, Any] | None = None, common: dict[str, Any] | None = None) -> None:
        findings = validate_instance(event, schema or self._event_schema(), schema_path=self.repository / "schemas/execution-event.schema.json", common_schema=common or self._common_schema(), schema_store=self._schema_store())
        if findings:
            finding = findings[0]
            raise DiagnosticError(_finding(finding.rule, finding.reason, file=self.log_path, line=line_number, location=finding.location, remediation=finding.remediation))

    def _validate_uri(self, uri: Any, *, location: str, file: Path) -> None:
        if not isinstance(uri, str):
            return
        policy = load_config(self.repository, "asset-policy.yaml")
        finding = validate_asset_uri(uri, allowed_schemes=policy.get("allowed_uri_schemes", ["urn", "https"]), allow_query=bool(policy.get("https", {}).get("allow_query", False)))
        if finding:
            raise DiagnosticError(_finding(finding.rule, finding.reason, file=file, location=location, remediation=finding.remediation))

    def _validate_record_schema(self, event_type: str, record: dict[str, Any]) -> None:
        schema_path = self.repository / "schemas" / RECORD_SCHEMAS[event_type][0]
        findings = validate_instance(record, self._record_schema(event_type), schema_path=schema_path, common_schema=self._common_schema(), schema_store=self._schema_store())
        if findings:
            raise DiagnosticError(findings[0])
        if record.get("project_id") != self._project_id():
            raise DiagnosticError(_finding("EXECUTION_PROJECT_MISMATCH", "record project_id does not match the materialized project", file=schema_path, location="/project_id", remediation="Use the project_id from manifest.yaml."))
        policy = load_config(self.repository, "safety-policy.yaml")
        security_findings = check_text_security(record, file=schema_path, forbidden_markers=policy.get("forbidden_markers", []), signed_url_markers=policy.get("signed_url_markers", []))
        if security_findings:
            finding = security_findings[0]
            raise DiagnosticError(_finding(finding.rule, finding.reason, file=schema_path, location=finding.location, remediation=finding.remediation))
        if event_type == "OUTPUT_VERSION_RECORDED":
            self._validate_uri(record["asset_ref"].get("uri"), location="/asset_ref/uri", file=schema_path)
        elif event_type == "INSTALLATION_PLAN_RECORDED":
            self._validate_uri(record["venue_ref"], location="/venue_ref", file=schema_path)
            self._validate_uri(record["external_effect_plan"]["target_ref"], location="/external_effect_plan/target_ref", file=schema_path)

    def _load_plan(self) -> dict[str, Any] | None:
        path = self.project_root / "03_plan/production-plan.yaml"
        if not path.is_file():
            return None
        plan = load_yaml(path)
        if not isinstance(plan, dict):
            raise DiagnosticError(_finding("EXECUTION_PLAN_OBJECT", "production-plan.yaml must be an object", file=path, remediation="Regenerate and validate the production plan."))
        findings = validate_plan_document(plan, repository=self.repository, plan_path=path)
        if findings:
            raise DiagnosticError(findings[0])
        return plan

    def _check_cross_references(self, event_type: str, record: dict[str, Any], records: dict[str, list[dict[str, Any]]]) -> None:
        plan = self._load_plan()
        if plan is None:
            return
        deliverables = {item.get("id") for item in plan.get("deliverables", []) if isinstance(item, dict)}
        tasks = {item.get("id") for item in plan.get("tasks", []) if isinstance(item, dict)}
        outputs = {(item.get("output_id"), item.get("revision")): item for item in records["OUTPUT_VERSION_RECORDED"]}
        output_ids = {item.get("output_id") for item in records["OUTPUT_VERSION_RECORDED"]}
        plans = {(item.get("installation_plan_id"), item.get("revision")): item for item in records["INSTALLATION_PLAN_RECORDED"]}
        if event_type == "OUTPUT_VERSION_RECORDED":
            if record["deliverable_id"] not in deliverables:
                raise DiagnosticError(_finding("EXECUTION_REFERENCE", "output deliverable_id is absent from the validated plan", file=self.project_root / "05_execution/output-versions.yaml", location="/deliverable_id", remediation="Use a deliverable declared by production-plan.yaml."))
            if not set(record["source_task_ids"]).issubset(tasks):
                raise DiagnosticError(_finding("EXECUTION_REFERENCE", "output source_task_ids contain an unknown task", file=self.project_root / "05_execution/output-versions.yaml", location="/source_task_ids", remediation="Use task IDs from production-plan.yaml."))
            if record["status"] == "AVAILABLE":
                quality_ids = set(record["quality_result_ids"])
                matching = [item for item in records["QUALITY_RESULT_RECORDED"] if item.get("quality_id") in quality_ids and item.get("output_id") == record["output_id"]]
                if not quality_ids or len(matching) != len(quality_ids) or any(item.get("status") != "PASS" for item in matching):
                    raise DiagnosticError(_finding("EXECUTION_OUTPUT_GATE", "AVAILABLE output requires a linked PASS quality result for this output", file=self.project_root / "05_execution/output-versions.yaml", location="/quality_result_ids", remediation="Record and link a PASS quality result before making the output available."))
            if record["status"] == "SUPERSEDED" and not record.get("supersedes"):
                raise DiagnosticError(_finding("EXECUTION_OUTPUT_GATE", "SUPERSEDED output requires a supersedes reference", file=self.project_root / "05_execution/output-versions.yaml", location="/supersedes", remediation="Reference the replaced output revision."))
        elif event_type == "QUALITY_RESULT_RECORDED":
            if (record["output_id"], record["output_revision"]) not in outputs:
                raise DiagnosticError(_finding("EXECUTION_REFERENCE", "quality result references an unknown output revision", file=self.project_root / "05_execution/quality-results.yaml", location="/output_id", remediation="Record the output version before recording its quality result."))
            results = [item["result"] for item in record["dimensions"]]
            if record["status"] == "NOT_RUN" and (record["executed_at"] is not None or record["evidence_refs"] or record["external_validation_status"] != "NOT_REQUIRED" or any(value != "NOT_RUN" for value in results)):
                raise DiagnosticError(_finding("EXECUTION_QUALITY_GUARD", "NOT_RUN quality results cannot claim execution, evidence, or a dimension result", file=self.project_root / "05_execution/quality-results.yaml", remediation="Keep every unexecuted quality field explicitly NOT_RUN or null."))
            if record["status"] == "PASS" and (record["executed_at"] is None or not record["evidence_refs"] or record["external_validation_status"] == "PENDING" or any(value != "PASS" for value in results)):
                raise DiagnosticError(_finding("EXECUTION_QUALITY_GUARD", "PASS quality requires execution time, evidence, and PASS for every dimension", file=self.project_root / "05_execution/quality-results.yaml", remediation="Complete the quality check or use EXTERNAL_VALIDATION_REQUIRED."))
            if record["status"] == "PASS":
                resolve_evidence_refs(
                    self.project_root,
                    self.repository,
                    record.get("evidence_refs"),
                    expected_targets={str(record.get("quality_id")), str(record.get("output_id"))},
                    file=self.project_root / "05_execution/quality-results.yaml",
                )
            if record["status"] == "EXTERNAL_VALIDATION_REQUIRED" and record["external_validation_status"] != "PENDING":
                raise DiagnosticError(_finding("EXECUTION_QUALITY_GUARD", "EXTERNAL_VALIDATION_REQUIRED quality must remain explicitly pending", file=self.project_root / "05_execution/quality-results.yaml", remediation="Set external_validation_status to PENDING."))
        elif event_type == "INSTALLATION_PLAN_RECORDED":
            if not set(record["output_ids"]).issubset(output_ids):
                raise DiagnosticError(_finding("EXECUTION_REFERENCE", "installation plan references an unknown output", file=self.project_root / "06_installation/installation-plan.yaml", location="/output_ids", remediation="Record the output version before planning installation."))
            effect = record["external_effect_plan"]
            if record["status"] == "APPROVED":
                if not record["approval_ids"] or effect["execution_status"] != "PLANNED":
                    raise DiagnosticError(_finding("EXECUTION_APPROVAL_GATE", "APPROVED installation requires approval IDs and a planned external effect", file=self.project_root / "06_installation/installation-plan.yaml", location="/approval_ids", remediation="Obtain explicit approval and freeze the external effect target before installation."))
                approval_path = self.project_root / "07_governance/approval-register.yaml"
                if approval_path.is_file():
                    approval_register = load_yaml(approval_path)
                    approved_ids = {item.get("approval_id") for item in (approval_register.get("approvals", []) if isinstance(approval_register, dict) else []) if isinstance(item, dict) and item.get("decision") == "APPROVED"}
                    if not set(record["approval_ids"]).issubset(approved_ids):
                        raise DiagnosticError(_finding("EXECUTION_APPROVAL_GATE", "installation plan references an approval that is not currently approved", file=approval_path, location="/approvals", remediation="Use an unexpired APPROVED record whose target matches the effect plan."))
        elif event_type == "INSTALLATION_RESULT_RECORDED":
            if not any(item.get("installation_plan_id") == record["installation_plan_id"] for item in records["INSTALLATION_PLAN_RECORDED"]):
                raise DiagnosticError(_finding("EXECUTION_REFERENCE", "installation result references an unknown installation plan", file=self.project_root / "06_installation/installation-results.yaml", location="/installation_plan_id", remediation="Record the installation plan before its result."))
            if not set(record["output_ids"]).issubset(output_ids):
                raise DiagnosticError(_finding("EXECUTION_REFERENCE", "installation result references an unknown output", file=self.project_root / "06_installation/installation-results.yaml", location="/output_ids", remediation="Use output IDs recorded in the execution register."))
            if record["status"] == "SUCCEEDED" and (record["safety_check_status"] != "PASS" or not record["evidence_refs"] or record.get("completed_at") is None):
                raise DiagnosticError(_finding("EXECUTION_INSTALLATION_GUARD", "SUCCEEDED installation requires safety PASS, completion time, and evidence", file=self.project_root / "06_installation/installation-results.yaml", remediation="Record external installation evidence and a completed safety check, or remain pending."))
            if record["status"] == "SUCCEEDED":
                resolve_evidence_refs(
                    self.project_root,
                    self.repository,
                    record.get("evidence_refs"),
                    expected_targets={str(record.get("installation_result_id")), str(record.get("installation_plan_id")), *{str(output_id) for output_id in record.get("output_ids", [])}},
                    file=self.project_root / "06_installation/installation-results.yaml",
                )
            if record["status"] == "EXTERNAL_VALIDATION_REQUIRED" and record["safety_check_status"] != "EXTERNAL_VALIDATION_REQUIRED":
                raise DiagnosticError(_finding("EXECUTION_INSTALLATION_GUARD", "pending installation validation must keep safety_check_status pending", file=self.project_root / "06_installation/installation-results.yaml", remediation="Use EXTERNAL_VALIDATION_REQUIRED for work not executed in this environment."))

    def _empty_records(self) -> dict[str, list[dict[str, Any]]]:
        return {event_type: [] for event_type in ALL_EVENT_TYPES}

    def _replay_records(self, events: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], int]:
        records = self._empty_records()
        previous_hash: str | None = None
        for expected_sequence, event in enumerate(events, start=1):
            if event.get("sequence") != expected_sequence:
                raise DiagnosticError(_finding("EXECUTION_SEQUENCE", f"expected event sequence {expected_sequence}, got {event.get('sequence')}", file=self.log_path, remediation="Restore contiguous immutable event ordering."))
            if event.get("previous_event_sha256") != previous_hash:
                raise DiagnosticError(_finding("EXECUTION_HASH_CHAIN", "previous_event_sha256 does not match the preceding event", file=self.log_path, remediation="Restore the original append-only event chain."))
            if event_sha256(event) != event.get("event_sha256"):
                raise DiagnosticError(_finding("EXECUTION_EVENT_HASH", "event_sha256 does not match the canonical event payload", file=self.log_path, remediation="Reject the tampered event and restore the canonical log."))
            event_type = event["type"]
            record = event["payload"]["record"]
            self._validate_record_schema(event_type, record)
            identity_field = RECORD_SCHEMAS[event_type][1]
            identity = (record[identity_field], record["revision"])
            if any((item.get(identity_field), item.get("revision")) == identity for item in records[event_type]):
                raise DiagnosticError(_finding("EXECUTION_RECORD_DUPLICATE", "the same immutable record identity appears more than once", file=self.log_path, remediation="Create a new revision or restore the append-only log."))
            records[event_type].append(deepcopy(record))
            self._check_cross_references(event_type, record, records)
            previous_hash = event["event_sha256"]
        return records, len(events)

    def _projection(self, event_type: str, records: list[dict[str, Any]], revision: int) -> dict[str, Any]:
        identity_field = RECORD_SCHEMAS[event_type][1]
        ordered = sorted(records, key=lambda item: (str(item.get(identity_field)), int(item.get("revision", 0))))
        value = {"schema_version": "1.0.0", "project_id": self._project_id(), "revision": revision, "records": ordered}
        value["integrity"] = {"content_sha256": canonical_sha256(value)}
        return value

    def _read_projection(self, event_type: str) -> dict[str, Any]:
        path = self._projection_path(event_type)
        if not path.is_file():
            raise DiagnosticError(_finding("EXECUTION_PROJECTION_MISSING", "execution projection is missing", file=path, remediation="Initialize the execution register or restore the complete projection set."))
        value = load_yaml(path)
        if not isinstance(value, dict):
            raise DiagnosticError(_finding("EXECUTION_PROJECTION_OBJECT", "execution projection must be a mapping", file=path, remediation="Regenerate the projection from production-log.jsonl."))
        findings = validate_instance(value, self._projection_schema(event_type), schema_path=self.repository / "schemas" / PROJECTION_FILES[event_type][1], common_schema=self._common_schema(), schema_store=self._schema_store())
        if findings:
            raise DiagnosticError(findings[0])
        if value.get("integrity", {}).get("content_sha256") != canonical_sha256(_without_integrity(value)):
            raise DiagnosticError(_finding("EXECUTION_STATE_DIVERGENCE", "execution projection content_sha256 does not match its canonical payload", file=path, remediation="Do not edit projections directly; replay the append-only execution log."))
        return value

    def init(self) -> dict[str, Any]:
        with self._writer_lock():
            projection_paths = [self._projection_path(event_type) for event_type in ALL_EVENT_TYPES]
            existing = [path for path in [self.log_path, *projection_paths] if path.exists()]
            if existing:
                if len(existing) != 1 + len(projection_paths):
                    raise DiagnosticError(_finding("EXECUTION_PARTIAL_STATE", "execution log and projections are only partially materialized", file=self.execution_root, remediation="Restore all execution files from the append-only source before retrying."))
                return self.replay()
            self.execution_root.mkdir(parents=True, exist_ok=True)
            self.installation_root.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("", encoding="utf-8")
            for event_type in ALL_EVENT_TYPES:
                self._write_yaml_atomic(self._projection_path(event_type), self._empty_projection())
            return self.replay()

    def replay(self) -> dict[str, Any]:
        events = self._read_events()
        records, revision = self._replay_records(events)
        for event_type in ALL_EVENT_TYPES:
            actual = self._read_projection(event_type)
            expected = self._projection(event_type, records[event_type], revision)
            if actual != expected:
                raise DiagnosticError(_finding("EXECUTION_STATE_DIVERGENCE", "materialized execution projection does not match the append-only log", file=self._projection_path(event_type), remediation="Do not edit projections directly; restore or replay production-log.jsonl."))
        return {"project_id": self._project_id(), "revision": revision, "events": revision, "records": {event_type: len(records[event_type]) for event_type in ALL_EVENT_TYPES}}

    def _new_event(self, event_type: str, record: dict[str, Any], *, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str, sequence: int, previous_hash: str | None) -> dict[str, Any]:
        _timestamp(occurred_at)
        event = {"event_id": f"EXE{sequence:06d}", "sequence": sequence, "occurred_at": occurred_at, "type": event_type, "actor": {"kind": actor_kind, "id": actor_id}, "idempotency_key": idempotency_key, "previous_event_sha256": previous_hash, "payload": {"record": deepcopy(record)}}
        event["event_sha256"] = event_sha256(event)
        return event

    def record(self, event_type: str, record: dict[str, Any], *, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str) -> dict[str, Any]:
        if event_type not in ALL_EVENT_TYPES:
            raise DiagnosticError(_finding("EXECUTION_EVENT_TYPE", f"unsupported execution event type {event_type!r}", file=self.log_path, remediation="Use one of the registered execution record types."))
        with self._writer_lock():
            self.replay()
            events = self._read_events()
            records, _ = self._replay_records(events)
            self._validate_record_schema(event_type, record)
            self._check_cross_references(event_type, record, records)
            candidate = self._new_event(event_type, record, occurred_at=occurred_at, actor_kind=actor_kind, actor_id=actor_id, idempotency_key=idempotency_key, sequence=len(events) + 1, previous_hash=events[-1]["event_sha256"] if events else None)
            self._validate_event_schema(candidate)
            for existing in events:
                if existing.get("idempotency_key") != idempotency_key:
                    continue
                if existing.get("type") == event_type and existing.get("actor") == candidate["actor"] and existing.get("payload") == candidate["payload"]:
                    return self.replay()
                raise DiagnosticError(_finding("EXECUTION_IDEMPOTENCY_MISMATCH", "idempotency key was already used with different execution content", file=self.log_path, remediation="Reuse the original content or choose a new idempotency key."))
            records[event_type].append(deepcopy(record))
            self._append_event(candidate)
            for current_type in ALL_EVENT_TYPES:
                self._write_yaml_atomic(self._projection_path(current_type), self._projection(current_type, records[current_type], len(events) + 1))
            return self.replay()

    def record_output(self, record: dict[str, Any], **kwargs: str) -> dict[str, Any]:
        return self.record("OUTPUT_VERSION_RECORDED", record, **kwargs)

    def record_quality(self, record: dict[str, Any], **kwargs: str) -> dict[str, Any]:
        return self.record("QUALITY_RESULT_RECORDED", record, **kwargs)

    def record_installation_plan(self, record: dict[str, Any], **kwargs: str) -> dict[str, Any]:
        return self.record("INSTALLATION_PLAN_RECORDED", record, **kwargs)

    def record_installation_result(self, record: dict[str, Any], **kwargs: str) -> dict[str, Any]:
        return self.record("INSTALLATION_RESULT_RECORDED", record, **kwargs)


def validate_execution_project(project_root: Path, repository: Path) -> list[Finding]:
    paths = [project_root / "05_execution/production-log.jsonl", project_root / "05_execution/output-versions.yaml", project_root / "05_execution/quality-results.yaml", project_root / "06_installation/installation-plan.yaml", project_root / "06_installation/installation-results.yaml"]
    if not any(path.exists() for path in paths):
        return []
    try:
        ExecutionManager(project_root, repository).replay()
        return []
    except DiagnosticError as exc:
        return [exc.finding]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return [_finding("EXECUTION_VALIDATION", str(exc), file=project_root / "05_execution", remediation="Restore the append-only execution log and its projections, then replay the register.")]
