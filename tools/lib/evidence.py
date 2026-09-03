"""Opaque evidence intake, append-only evidence events, and register replay."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import fcntl

from .canonical import canonical_sha256, event_sha256
from .config import load_config
from .diagnostics import DiagnosticError, Finding
from .schema import load_schema, validate_instance
from .security import check_text_security, validate_asset_uri
from .yaml_io import dump_yaml, load_jsonl, load_yaml


EVIDENCE_LOG = "05_execution/evidence-log.jsonl"
EVIDENCE_REGISTER = "05_execution/evidence-register.yaml"


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _without_integrity(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "integrity"}


def _timestamp(value: str) -> None:
    from datetime import datetime

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset")


class EvidenceManager:
    """Single-writer evidence log and deterministic register projection."""

    def __init__(self, project_root: Path, repository: Path):
        self.project_root = project_root.resolve()
        self.repository = repository.resolve()
        self.execution_root = self.project_root / "05_execution"
        self.log_path = self.project_root / EVIDENCE_LOG
        self.register_path = self.project_root / EVIDENCE_REGISTER
        self.lock_path = self.execution_root / ".evidence.lock"

    def _project_id(self) -> str:
        manifest = load_yaml(self.project_root / "manifest.yaml")
        if isinstance(manifest, dict) and isinstance(manifest.get("project_id"), str):
            return manifest["project_id"]
        raise DiagnosticError(_finding("EVIDENCE_PROJECT_ID", "project_id is missing from manifest.yaml", file=self.project_root / "manifest.yaml", remediation="Restore a valid materialized production manifest."))

    def _common_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas/common.schema.json")

    def _record_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas/evidence-record.schema.json")

    def _event_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas/evidence-event.schema.json")

    def _register_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas/evidence-register.schema.json")

    def _schema_store(self) -> list[dict[str, Any]]:
        return [self._record_schema(), self._register_schema()]

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

    def _validate_uri(self, uri: Any, *, file: Path, location: str) -> None:
        if not isinstance(uri, str):
            return
        policy = load_config(self.repository, "asset-policy.yaml")
        finding = validate_asset_uri(
            uri,
            allowed_schemes=policy.get("allowed_uri_schemes", ["urn", "https"]),
            allow_query=False,
        )
        if finding:
            raise DiagnosticError(_finding(finding.rule, finding.reason, file=file, location=location, remediation=finding.remediation))
        parsed = urlparse(uri)
        if parsed.username is not None or parsed.password is not None:
            raise DiagnosticError(_finding("EVIDENCE_URI_USERINFO", "evidence URI must not contain userinfo", file=file, location=location, remediation="Use an opaque URI without embedded credentials."))
        if uri.startswith("/") or (len(uri) > 1 and uri[1] == ":" and uri[0].isalpha()):
            raise DiagnosticError(_finding("EVIDENCE_URI_LOCAL_PATH", "evidence URI must not be a local absolute path", file=file, location=location, remediation="Store only an opaque urn or HTTPS reference; do not expose local filesystem paths."))

    def _validate_record(self, record: dict[str, Any], *, file: Path | None = None) -> None:
        source = file or self.register_path
        findings = validate_instance(
            record,
            self._record_schema(),
            schema_path=self.repository / "schemas/evidence-record.schema.json",
            common_schema=self._common_schema(),
            schema_store=self._schema_store(),
        )
        if findings:
            raise DiagnosticError(findings[0])
        if record.get("project_id") != self._project_id():
            raise DiagnosticError(_finding("EVIDENCE_PROJECT_MISMATCH", "evidence project_id does not match the materialized project", file=source, location="/project_id", remediation="Use the project_id from manifest.yaml."))
        targets = record.get("target_refs", [])
        if any(not isinstance(target, str) or not target or any(marker in target for marker in ("*", "?")) for target in targets):
            raise DiagnosticError(_finding("EVIDENCE_TARGET_WILDCARD", "evidence target_refs must identify exact targets without wildcards", file=source, location="/target_refs", remediation="Record one or more exact production IDs as target_refs."))
        self._validate_uri(record.get("uri"), file=source, location="/uri")
        if record.get("verification_status") == "VERIFIED":
            for field in ("target_refs", "content_sha256", "verification_method", "limitations"):
                value = record.get(field)
                if not value:
                    raise DiagnosticError(_finding("EVIDENCE_VERIFIED_GUARD", f"VERIFIED evidence requires non-empty {field}", file=source, location=f"/{field}", remediation="Supply the exact target, content hash, verification method, and limitations before marking evidence VERIFIED."))
        policy = load_config(self.repository, "safety-policy.yaml")
        security_findings = check_text_security(record, file=source, forbidden_markers=policy.get("forbidden_markers", []), signed_url_markers=policy.get("signed_url_markers", []))
        if security_findings:
            finding = security_findings[0]
            raise DiagnosticError(_finding(finding.rule, finding.reason, file=source, location=finding.location, remediation=finding.remediation))

    def _validate_event(self, event: dict[str, Any], *, line_number: int | None = None) -> None:
        findings = validate_instance(
            event,
            self._event_schema(),
            schema_path=self.repository / "schemas/evidence-event.schema.json",
            common_schema=self._common_schema(),
            schema_store=self._schema_store(),
        )
        if findings:
            finding = findings[0]
            raise DiagnosticError(_finding(finding.rule, finding.reason, file=self.log_path, location=finding.location, remediation=finding.remediation))
        self._validate_record(event["payload"]["record"], file=self.log_path)

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.log_path.is_file() or self.log_path.stat().st_size == 0:
            return []
        if not self.log_path.read_bytes().endswith(b"\n"):
            raise DiagnosticError(_finding("EVIDENCE_PARTIAL_LINE", "evidence-log.jsonl does not end with a complete newline-terminated event", file=self.log_path, remediation="Restore the complete final event; never truncate the audit log automatically."))
        try:
            records = load_jsonl(self.log_path)
        except DiagnosticError as exc:
            raise DiagnosticError(_finding("EVIDENCE_LOG_SYNTAX", exc.finding.reason, file=self.log_path, remediation="Restore a valid JSON object on every evidence log line.")) from exc
        events: list[dict[str, Any]] = []
        for line_number, event in records:
            self._validate_event(event, line_number=line_number)
            events.append(event)
        return events

    def _projection(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        records = [deepcopy(event["payload"]["record"]) for event in events]
        records.sort(key=lambda item: (str(item.get("evidence_id")), int(item.get("revision", 0))))
        value = {"schema_version": "1.0.0", "project_id": self._project_id(), "revision": len(events), "records": records}
        value["integrity"] = {"content_sha256": canonical_sha256(value)}
        return value

    def _read_register(self) -> dict[str, Any]:
        if not self.register_path.is_file():
            raise DiagnosticError(_finding("EVIDENCE_REGISTER_MISSING", "evidence-register.yaml is missing", file=self.register_path, remediation="Initialize or restore the evidence register projection."))
        value = load_yaml(self.register_path)
        if not isinstance(value, dict):
            raise DiagnosticError(_finding("EVIDENCE_REGISTER_OBJECT", "evidence-register.yaml must be a mapping", file=self.register_path, remediation="Regenerate the register from evidence-log.jsonl."))
        findings = validate_instance(value, self._register_schema(), schema_path=self.repository / "schemas/evidence-register.schema.json", common_schema=self._common_schema(), schema_store=self._schema_store())
        if findings:
            raise DiagnosticError(findings[0])
        if value.get("integrity", {}).get("content_sha256") != canonical_sha256(_without_integrity(value)):
            raise DiagnosticError(_finding("EVIDENCE_STATE_DIVERGENCE", "evidence register content_sha256 does not match its canonical payload", file=self.register_path, remediation="Do not edit the evidence register directly; replay the append-only evidence log."))
        return value

    def init(self) -> dict[str, Any]:
        with self._writer_lock():
            existing = [path for path in (self.log_path, self.register_path) if path.exists()]
            if existing:
                if len(existing) != 2:
                    raise DiagnosticError(_finding("EVIDENCE_PARTIAL_STATE", "evidence log and register are only partially materialized", file=self.execution_root, remediation="Restore both evidence files from the append-only source before retrying."))
                return self.replay()
            self.execution_root.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("", encoding="utf-8")
            self._write_yaml_atomic(self.register_path, self._projection([]))
            return self.replay()

    def _replay(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        previous_hash: str | None = None
        identities: set[tuple[str, int]] = set()
        for expected_sequence, event in enumerate(events, start=1):
            if event.get("sequence") != expected_sequence:
                raise DiagnosticError(_finding("EVIDENCE_SEQUENCE", f"expected event sequence {expected_sequence}, got {event.get('sequence')}", file=self.log_path, remediation="Restore contiguous immutable evidence event ordering."))
            if event.get("previous_event_sha256") != previous_hash:
                raise DiagnosticError(_finding("EVIDENCE_HASH_CHAIN", "previous_event_sha256 does not match the preceding evidence event", file=self.log_path, remediation="Restore the original append-only evidence chain."))
            if event_sha256(event) != event.get("event_sha256"):
                raise DiagnosticError(_finding("EVIDENCE_EVENT_HASH", "event_sha256 does not match the canonical evidence event", file=self.log_path, remediation="Reject the tampered event and restore the canonical log."))
            record = event["payload"]["record"]
            identity = (record["evidence_id"], record["revision"])
            if identity in identities:
                raise DiagnosticError(_finding("EVIDENCE_RECORD_DUPLICATE", "the same evidence identity appears more than once", file=self.log_path, remediation="Create a new evidence revision or restore the append-only log."))
            identities.add(identity)
            previous_hash = event["event_sha256"]
        return {"events": len(events), "identities": identities}

    def replay(self) -> dict[str, Any]:
        existing = [path for path in (self.log_path, self.register_path) if path.exists()]
        if len(existing) != 2:
            raise DiagnosticError(_finding("EVIDENCE_PARTIAL_STATE", "evidence log and register must be materialized together", file=self.execution_root, remediation="Restore both evidence files from the append-only source before replaying or recording evidence."))
        events = self._read_events()
        replayed = self._replay(events)
        actual = self._read_register()
        expected = self._projection(events)
        if actual != expected:
            raise DiagnosticError(_finding("EVIDENCE_STATE_DIVERGENCE", "materialized evidence register does not match the append-only evidence log", file=self.register_path, remediation="Do not edit the register directly; restore or replay evidence-log.jsonl."))
        return {"project_id": self._project_id(), "revision": len(events), "events": replayed["events"], "records": len(actual["records"])}

    def record_evidence(self, record: dict[str, Any], *, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key:
            raise DiagnosticError(_finding("EVIDENCE_IDEMPOTENCY_KEY", "idempotency_key is required", file=self.log_path, remediation="Provide a stable idempotency key for the evidence receipt."))
        _timestamp(occurred_at)
        with self._writer_lock():
            self.replay()
            events = self._read_events()
            self._validate_record(record)
            if record.get("recorded_at") != occurred_at:
                raise DiagnosticError(_finding("EVIDENCE_RECORDED_AT", "recorded_at must equal the evidence event occurred_at", file=self.register_path, location="/recorded_at", remediation="Use the same explicit RFC 3339 timestamp for recording the evidence and its event."))
            recorded_by = record.get("recorded_by")
            if recorded_by != {"kind": actor_kind, "id": actor_id}:
                raise DiagnosticError(_finding("EVIDENCE_ACTOR_MISMATCH", "recorded_by must match the evidence event actor", file=self.register_path, location="/recorded_by", remediation="Use the actor that actually recorded the evidence metadata."))
            identity = (record.get("evidence_id"), record.get("revision"))
            for existing in events:
                if existing.get("idempotency_key") == idempotency_key:
                    if existing.get("type") == "EVIDENCE_RECORDED" and existing.get("actor") == {"kind": actor_kind, "id": actor_id} and existing.get("payload", {}).get("record") == record:
                        return self.replay()
                    raise DiagnosticError(_finding("EVIDENCE_IDEMPOTENCY_MISMATCH", "idempotency key was already used with different evidence content", file=self.log_path, remediation="Reuse the original evidence content or choose a new idempotency key."))
                existing_record = existing.get("payload", {}).get("record", {})
                if (existing_record.get("evidence_id"), existing_record.get("revision")) == identity:
                    raise DiagnosticError(_finding("EVIDENCE_RECORD_IMMUTABLE", "evidence_id and revision already exist with different content", file=self.log_path, remediation="Use a new revision for changed evidence metadata."))
            sequence = len(events) + 1
            event: dict[str, Any] = {
                "event_id": f"EVE{sequence:06d}",
                "sequence": sequence,
                "occurred_at": occurred_at,
                "type": "EVIDENCE_RECORDED",
                "actor": {"kind": actor_kind, "id": actor_id},
                "idempotency_key": idempotency_key,
                "previous_event_sha256": events[-1]["event_sha256"] if events else None,
                "payload": {"record": deepcopy(record)},
            }
            event["event_sha256"] = event_sha256(event)
            self._validate_event(event)
            self._append_event(event)
            self._write_yaml_atomic(self.register_path, self._projection([*events, event]))
            return self.replay()

    def records(self) -> list[dict[str, Any]]:
        self.replay()
        return deepcopy(self._read_register()["records"])


def resolve_evidence_refs(
    project_root: Path,
    repository: Path,
    refs: Any,
    *,
    expected_targets: set[str] | None = None,
    file: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Resolve exact object references to VERIFIED evidence records."""

    source = file or project_root / EVIDENCE_REGISTER
    if not isinstance(refs, list) or not refs:
        raise DiagnosticError(_finding("EVIDENCE_REFERENCE_SHAPE", "evidence references must be a non-empty list of {evidence_id, revision} objects", file=source, remediation="Register evidence first and use its exact evidence_id and revision."))
    normalized: list[tuple[str, int]] = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or set(ref) != {"evidence_id", "revision"} or not isinstance(ref.get("evidence_id"), str) or not isinstance(ref.get("revision"), int) or isinstance(ref.get("revision"), bool) or ref["revision"] < 1:
            raise DiagnosticError(_finding("EVIDENCE_REFERENCE_SHAPE", "evidence references must be exact {evidence_id, revision} objects", file=source, location=f"/evidence_refs/{index}", remediation="Replace URI strings or extra fields with an exact evidence reference object."))
        normalized.append((ref["evidence_id"], ref["revision"]))
    manager = EvidenceManager(project_root, repository)
    try:
        records = manager.records()
    except DiagnosticError as exc:
        if exc.finding.rule in {"EVIDENCE_PARTIAL_STATE", "EVIDENCE_REGISTER_MISSING"}:
            raise DiagnosticError(_finding("EVIDENCE_REGISTER_MISSING", "evidence reference cannot resolve because the evidence register is not materialized", file=source, remediation="Initialize the evidence register and record the evidence metadata before claiming success.")) from exc
        raise
    index = {(record.get("evidence_id"), record.get("revision")): record for record in records}
    resolved: list[dict[str, Any]] = []
    for evidence_id, revision in normalized:
        record = index.get((evidence_id, revision))
        if record is None:
            raise DiagnosticError(_finding("EVIDENCE_UNREGISTERED", f"evidence {evidence_id} revision {revision} is not registered", file=source, remediation="Record the exact evidence metadata before using the reference."))
        if record.get("verification_status") != "VERIFIED":
            raise DiagnosticError(_finding("EVIDENCE_NOT_VERIFIED", f"evidence {evidence_id} revision {revision} is not VERIFIED", file=source, remediation="Verify the evidence or keep the dependent production state pending."))
        if expected_targets is not None and not expected_targets.intersection(set(record.get("target_refs", []))):
            raise DiagnosticError(_finding("EVIDENCE_TARGET_MISMATCH", f"evidence {evidence_id} does not target the required production ID", file=source, remediation="Use evidence whose target_refs contains the exact output, task, effect, quality, installation, or acceptance-test ID."))
        resolved.append(deepcopy(record))
    return resolved


def validate_evidence_project(project_root: Path, repository: Path) -> list[Finding]:
    paths = [project_root / EVIDENCE_LOG, project_root / EVIDENCE_REGISTER]
    if not any(path.exists() for path in paths):
        return []
    try:
        EvidenceManager(project_root, repository).replay()
        return []
    except DiagnosticError as exc:
        return [exc.finding]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return [_finding("EVIDENCE_VALIDATION", str(exc), file=project_root / "05_execution", remediation="Restore the append-only evidence log and its register projection, then replay the register.")]
