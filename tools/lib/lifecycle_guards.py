"""Fail-closed lifecycle transition guards.

The runtime event log is the append-only source of lifecycle state, but the
conditions for advancing that state live in the canonical project records.
This module deliberately does not trust transition payloads for those
conditions.  Payloads carry the decision metadata and the event fixes the
canonical record hashes used by the guard.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .canonical import canonical_sha256, sha256_bytes
from .config import load_config
from .diagnostics import DiagnosticError, Finding
from .evidence import resolve_evidence_refs
from .result import validate_completion_report, validate_result
from .yaml_io import load_json, load_yaml

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Runtime


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str, context: dict[str, Any] | None = None) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation, context=context or {})


class LifecycleGuardEvaluator:
    """Evaluate one state transition against the project canonical records."""

    _ACTIVE_STATES = {
        "HANDOFF_VALIDATED",
        "PLANNING",
        "READY_FOR_PROTOTYPE",
        "PROTOTYPING",
        "REVIEWING",
        "READY_FOR_PRODUCTION",
        "PRODUCING",
        "READY_FOR_INSTALLATION",
        "INSTALLING",
        "VALIDATING",
    }
    _TERMINAL_STATES = {"COMPLETE", "COMPLETE_WITH_GAPS"}

    def __init__(self, runtime: "Runtime"):
        self.runtime = runtime
        self.project_root = runtime.project_root
        self.repository = runtime.repository
        self.state_path = runtime.state_path
        self.log_path = runtime.log_path
        self.plan_path = self.project_root / "03_plan/production-plan.yaml"
        self.control_path = self.project_root / "04_prototype/prototype-control.yaml"
        self.receipt_path = self.project_root / "00_handoff/handoff-receipt.yaml"
        self.handoff_path = self.project_root / "00_handoff/production-handoff.yaml"
        self.manifest_path = self.project_root / "manifest.yaml"
        self._mapping_cache: dict[Path, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._plan_cache: tuple[tuple[int, int], dict[str, Any]] | None = None
        self._evidence_cache: dict[tuple[str, str], tuple[tuple[tuple[str, int, int], ...], dict[str, Any]]] = {}

    @staticmethod
    def _signature(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size

    def _fail(self, rule: str, reason: str, path: Path, *, location: str | None = None, remediation: str, context: dict[str, Any] | None = None) -> None:
        raise DiagnosticError(_finding(rule, reason, file=path, location=location, remediation=remediation, context=context))

    def _mapping(self, path: Path, *, rule: str, required: bool = True) -> dict[str, Any]:
        if not path.is_file():
            if required:
                self._fail(rule, "canonical lifecycle record is missing", path, remediation="Materialize the required canonical record before requesting this lifecycle transition.")
            return {}
        signature = self._signature(path)
        cached = self._mapping_cache.get(path)
        if cached is not None and cached[0] == signature:
            return deepcopy(cached[1])
        value = load_json(path) if path.suffix == ".json" else load_yaml(path)
        if not isinstance(value, dict):
            self._fail(rule, "canonical lifecycle record must be a mapping", path, remediation="Regenerate the canonical record as a mapping and validate the project again.")
        self._mapping_cache[path] = (signature, deepcopy(value))
        return value

    def _records(self, path: Path, key: str, *, rule: str, required: bool = False) -> list[dict[str, Any]]:
        value = self._mapping(path, rule=rule, required=required)
        records = value.get(key, [])
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            self._fail(rule, f"{key} must be a list of canonical records", path, location=f"/{key}", remediation="Regenerate the register projection from its append-only log.")
        return [item for item in records if isinstance(item, dict)]

    def _manifest_immutable(self) -> dict[str, Any]:
        manifest = self._mapping(self.manifest_path, rule="RUNTIME_HANDOFF_GUARD")
        # Runtime owns this projection field and changes it after every event.
        manifest.pop("state", None)
        return manifest

    def _plan(self) -> dict[str, Any]:
        signature: tuple[int, int] | None = None
        if self.plan_path.is_file():
            signature = self._signature(self.plan_path)
            if self._plan_cache is not None and self._plan_cache[0] == signature:
                return deepcopy(self._plan_cache[1])
        try:
            plan = self.runtime._load_plan()
            if signature is None:
                signature = self._signature(self.plan_path)
            self._plan_cache = (signature, deepcopy(plan))
            return plan
        except DiagnosticError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as exc:
            self._fail("RUNTIME_PLAN_GUARD", str(exc), self.plan_path, remediation="Regenerate and validate the production plan before advancing the lifecycle.")
        return {}

    def _control(self, *, required: bool = True) -> dict[str, Any]:
        return self._mapping(self.control_path, rule="RUNTIME_PROTOTYPE_GUARD", required=required)

    def _plan_gaps(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        return [gap for gap in plan.get("gaps", []) if isinstance(gap, dict)]

    def _blocking_gaps(self, plan: dict[str, Any], control: dict[str, Any] | None = None) -> list[str]:
        gaps = self._plan_gaps(plan)
        if isinstance(control, dict):
            for collection in ("runs", "test_results", "reviews"):
                for record in control.get(collection, []):
                    if isinstance(record, dict) and isinstance(record.get("gap"), dict):
                        gaps.append(record["gap"])
        return sorted({str(gap.get("id")) for gap in gaps if gap.get("blocking") is True and gap.get("id")})

    def _verify_evidence(self, refs: Any, *, expected_targets: set[str], rule: str, path: Path | None = None) -> list[dict[str, Any]]:
        try:
            return resolve_evidence_refs(self.project_root, self.repository, refs, expected_targets=expected_targets, file=path or self.state_path)
        except DiagnosticError as exc:
            self._fail(rule, exc.finding.reason, Path(exc.finding.file or path or self.state_path), location=exc.finding.location, remediation=exc.finding.remediation, context={"cause": exc.finding.rule})
        return []

    def _check_handoff_to_planning(self) -> None:
        receipt = self._mapping(self.receipt_path, rule="RUNTIME_HANDOFF_GUARD")
        handoff = self._mapping(self.handoff_path, rule="RUNTIME_HANDOFF_GUARD")
        manifest = self._mapping(self.manifest_path, rule="RUNTIME_HANDOFF_GUARD")
        if receipt.get("receipt_status") != "ACCEPTED":
            self._fail("RUNTIME_HANDOFF_GUARD", "HANDOFF_VALIDATED -> PLANNING requires an ACCEPTED receipt", self.receipt_path, location="/receipt_status", remediation="Accept the immutable handoff receipt before bootstrapping the runtime.")
        receipt_key = (receipt.get("handoff_id"), receipt.get("revision"), receipt.get("handoff_sha256"))
        manifest_key = (manifest.get("handoff", {}).get("handoff_id"), manifest.get("handoff", {}).get("revision"), manifest.get("handoff", {}).get("content_sha256"))
        handoff_key = (handoff.get("handoff_id"), handoff.get("revision"), handoff.get("integrity", {}).get("content_sha256"))
        if receipt_key != manifest_key or receipt_key != handoff_key:
            self._fail("RUNTIME_HANDOFF_GUARD", "receipt, project manifest, and accepted handoff key/hash do not match", self.receipt_path, location="/handoff_sha256", remediation="Re-accept one self-contained handoff bundle and regenerate the project manifest.", context={"receipt": receipt_key, "manifest": manifest_key, "handoff": handoff_key})
        if not receipt.get("schema_version") or not isinstance(receipt.get("rules"), list):
            self._fail("RUNTIME_HANDOFF_GUARD", "handoff receipt does not contain the required schema snapshot", self.receipt_path, location="/schema_version", remediation="Regenerate the accepted receipt with schema_version and validation rules.")

    def _check_prototype_plan(self, *, require_control_ready: bool) -> None:
        plan = self._plan()
        scope = plan.get("scope_baseline", {})
        prototype_ids = scope.get("prototype_plan_ids")
        if not isinstance(prototype_ids, list) or not prototype_ids:
            self._fail("RUNTIME_PROTOTYPE_GUARD", "prototype target is not present in the validated plan", self.plan_path, location="/scope_baseline/prototype_plan_ids", remediation="Add the accepted prototype plan and rebuild the production plan.")
        control = self._control(required=require_control_ready)
        if control:
            if control.get("state") != "READY_FOR_PROTOTYPE":
                self._fail("RUNTIME_PROTOTYPE_GUARD", "prototype control is not READY_FOR_PROTOTYPE", self.control_path, location="/state", remediation="Resolve prototype task, test, resource, risk, and approval gates before starting prototype execution.")
            plan_ref = control.get("plan_ref", {})
            expected_ref = {"id": plan.get("plan_id"), "revision": plan.get("plan_revision"), "content_sha256": plan.get("integrity", {}).get("content_sha256")}
            if any(plan_ref.get(key) != value for key, value in expected_ref.items()):
                self._fail("RUNTIME_PROTOTYPE_GUARD", "prototype control plan_ref does not match the validated production plan", self.control_path, location="/plan_ref", remediation="Regenerate prototype control from the current validated plan revision.")
            if not control.get("runs"):
                self._fail("RUNTIME_PROTOTYPE_GUARD", "prototype target has no execution run", self.control_path, location="/runs", remediation="Generate at least one prototype run for the selected prototype plan.")
            blocking = self._blocking_gaps(plan, control)
            if blocking:
                self._fail("RUNTIME_PROTOTYPE_GUARD", "prototype stage still has blocking gaps", self.control_path, location="/runs", remediation="Resolve every blocking prototype gap before advancing.", context={"gap_ids": blocking})
        elif require_control_ready:
            self._fail("RUNTIME_PROTOTYPE_GUARD", "prototype control is required for this transition", self.control_path, remediation="Build and validate prototype-control.yaml before advancing.")

    def _check_prototyping_start(self, state: dict[str, Any], occurred_at: str) -> None:
        control = self._control()
        if control.get("state") != "READY_FOR_PROTOTYPE":
            self._fail("RUNTIME_PROTOTYPE_START_GUARD", "prototype execution requires READY_FOR_PROTOTYPE control", self.control_path, location="/state", remediation="Return the prototype control to READY_FOR_PROTOTYPE after resolving its gates.")
        runs = [run for run in control.get("runs", []) if isinstance(run, dict)]
        if not runs:
            self._fail("RUNTIME_PROTOTYPE_START_GUARD", "no prototype run is eligible", self.control_path, location="/runs", remediation="Record an eligible prototype run with its task, resources, approvals, and stopping limits.")
        plan = self._plan()
        resources = {str(item.get("id")): item for item in plan.get("resources", []) if isinstance(item, dict)}
        materials = {str(item.get("id")): item for item in plan.get("materials", []) if isinstance(item, dict)}
        for run in runs:
            status = run.get("status")
            if status in {"COMPLETE", "FAILED", "SKIPPED"}:
                if status == "COMPLETE" and (run.get("external_validation_status") != "VERIFIED" or not run.get("evidence_refs")):
                    self._fail("RUNTIME_PROTOTYPE_START_GUARD", "terminal prototype run is not backed by VERIFIED evidence", self.control_path, location=f"/runs/{run.get('id')}", remediation="Register VERIFIED evidence for the exact prototype run or keep it non-terminal.")
                continue
            if status not in {"PLANNED", "IN_PROGRESS"}:
                self._fail("RUNTIME_PROTOTYPE_START_GUARD", f"prototype run has non-executable status {status!r}", self.control_path, location=f"/runs/{run.get('id')}/status", remediation="Use PLANNED or IN_PROGRESS only when entering prototype execution.")
            for task_id in run.get("production_task_ids", []):
                task = next((item for item in plan.get("tasks", []) if isinstance(item, dict) and item.get("id") == task_id), None)
                if not isinstance(task, dict):
                    self._fail("RUNTIME_PROTOTYPE_START_GUARD", f"prototype run references unknown task {task_id!r}", self.control_path, remediation="Regenerate the prototype control from the validated plan.")
                for resource_id in task.get("required_resource_ids", []):
                    if resources.get(resource_id, {}).get("availability") != "AVAILABLE":
                        self._fail("RUNTIME_PROTOTYPE_START_GUARD", f"prototype resource {resource_id} is not AVAILABLE", self.plan_path, location="/resources", remediation="Resolve the resource availability gate before starting the run.")
                for material_id in task.get("required_material_ids", []):
                    if materials.get(material_id, {}).get("status") != "APPROVED":
                        self._fail("RUNTIME_PROTOTYPE_START_GUARD", f"prototype material {material_id} is not APPROVED", self.plan_path, location="/materials", remediation="Approve the exact material revision before starting the run.")
                if task.get("approval_requirement_ids"):
                    try:
                        self.runtime._valid_approval_ids(state, list(task.get("approval_requirement_ids", [])), occurred_at=occurred_at, plan=plan)
                    except DiagnosticError as exc:
                        self._fail("RUNTIME_PROTOTYPE_START_GUARD", exc.finding.reason, self.plan_path, remediation=exc.finding.remediation, context={"cause": exc.finding.rule, "task_id": task_id})
        limits = load_config(self.repository, "stopping-policy.yaml")
        if len(runs) > int(limits.get("max_iteration_count", 0)):
            self._fail("RUNTIME_PROTOTYPE_START_GUARD", "prototype iteration count exceeds the configured stopping limit", self.control_path, location="/runs", remediation="Stop and create a reviewed next iteration instead of exceeding the configured limit.")

    def _check_prototype_review(self, payload: dict[str, Any], state: dict[str, Any]) -> None:
        control = self._control()
        runs = [run for run in control.get("runs", []) if isinstance(run, dict)]
        if not runs or any(run.get("status") not in {"COMPLETE", "FAILED", "SKIPPED"} for run in runs):
            self._fail("RUNTIME_PROTOTYPE_REVIEW_GUARD", "all prototype runs must be terminal before REVIEWING", self.control_path, location="/runs", remediation="Complete, fail, or explicitly skip every linked prototype run.")
        test_by_id = {str(item.get("id")): item for item in control.get("test_results", []) if isinstance(item, dict)}
        for run in runs:
            if run.get("status") == "COMPLETE":
                self._verify_evidence(run.get("evidence_refs"), expected_targets={str(run.get("id")), self.runtime._project_id()}, rule="RUNTIME_PROTOTYPE_REVIEW_GUARD", path=self.control_path)
            for test_id in run.get("test_result_ids", []):
                test = test_by_id.get(str(test_id))
                if not isinstance(test, dict):
                    self._fail("RUNTIME_PROTOTYPE_REVIEW_GUARD", f"prototype run references missing test result {test_id!r}", self.control_path, remediation="Record the linked prototype test result before review.")
                if test.get("result") == "PASS":
                    self._verify_evidence(test.get("evidence_refs"), expected_targets={str(test.get("id")), str(test.get("acceptance_test_id")), str(run.get("id"))}, rule="RUNTIME_PROTOTYPE_REVIEW_GUARD", path=self.control_path)
        if any(effect.get("status") in {"UNKNOWN", "STARTED"} for effect in state.get("effects", {}).values() if isinstance(effect, dict)):
            self._fail("RUNTIME_PROTOTYPE_REVIEW_GUARD", "prototype review cannot proceed with an UNKNOWN or in-flight effect", self.state_path, location="/effects", remediation="Reconcile every external effect and attach VERIFIED evidence before review.")
        evidence = payload.get("external_evidence_refs")
        self._verify_evidence(evidence, expected_targets={self.runtime._project_id()} | {str(run.get("id")) for run in runs}, rule="RUNTIME_PROTOTYPE_REVIEW_GUARD")

    def _review_outcome(self, control: dict[str, Any]) -> tuple[bool, bool]:
        reviews = [review for review in control.get("reviews", []) if isinstance(review, dict)]
        decisions = [decision for decision in control.get("iteration_decisions", []) if isinstance(decision, dict)]
        failed = any(review.get("overall_result") in {"FAIL", "CONDITIONAL", "DEVIATION"} for review in reviews) or any(decision.get("decision") in {"REVISE", "BLOCK"} for decision in decisions)
        proceed = any(review.get("status") == "COMPLETE" and review.get("overall_result") == "PASS" for review in reviews) and any(decision.get("decision") == "PROCEED" and decision.get("status") == "APPROVED" for decision in decisions)
        return failed, proceed

    def _check_review_to_planning(self, payload: dict[str, Any]) -> None:
        control = self._control()
        failed, _ = self._review_outcome(control)
        if not failed:
            self._fail("RUNTIME_REVIEW_GUARD", "review did not record FAIL, deviation, or a revision/block decision", self.control_path, location="/reviews", remediation="Record the failed review outcome and its iteration/change request in prototype control.")
        for key in ("impact", "baseline_revision", "change_request_id"):
            if not payload.get(key):
                self._fail("RUNTIME_REVIEW_GUARD", f"review return is missing {key}", self.state_path, location=f"/payload/{key}", remediation="Record impact, the baseline revision, and the linked change request before returning to planning.")

    def _check_production_readiness(self, *, require_review: bool) -> None:
        plan = self._plan()
        selection = plan.get("selection_record", {})
        if selection.get("status") != "HUMAN_SELECTED" or selection.get("authority") != "HUMAN":
            self._fail("RUNTIME_PRODUCTION_READINESS_GUARD", "production readiness requires a HUMAN-selected scope", self.plan_path, location="/selection_record", remediation="Record the human selection for the exact accepted handoff scope before production.")
        required = {
            "scope_baseline": plan.get("scope_baseline", {}).get("status") == "BASELINED",
            "specification": bool(plan.get("technical_specifications")) and all(item.get("status") == "BASELINED" for item in plan.get("technical_specifications", []) if isinstance(item, dict)),
            "wbs": bool(plan.get("work_packages")) and all(item.get("status") != "BLOCKED" for item in plan.get("work_packages", []) if isinstance(item, dict)),
            "budget": plan.get("budget", {}).get("status") in {"QUOTED", "RESERVED", "COMMITTED", "ACTUAL"},
            "schedule": plan.get("schedule", {}).get("baseline_status") == "BASELINED",
            "risk": all(item.get("status") != "OPEN" or item.get("blocking") is not True for item in plan.get("risks", []) if isinstance(item, dict)),
        }
        missing = sorted(key for key, passed in required.items() if not passed)
        control = self._control()
        if require_review:
            failed, proceed = self._review_outcome(control)
            dimensions = {str(assessment.get("dimension")): assessment.get("result") for review in control.get("reviews", []) if isinstance(review, dict) for assessment in review.get("assessments", []) if isinstance(assessment, dict)}
            mandatory_dimensions = {"TECHNICAL", "ARTISTIC", "REQUIREMENT", "RIGHTS_PRIVACY", "FEASIBILITY", "SAFETY"}
            if failed or not proceed or any(dimensions.get(dimension) != "PASS" for dimension in mandatory_dimensions):
                missing.append("review")
        if not any(decision.get("decision") == "PROCEED" and decision.get("status") == "APPROVED" for decision in control.get("iteration_decisions", []) if isinstance(decision, dict)):
            missing.append("prototype_decision")
        blocking = self._blocking_gaps(plan, control)
        if blocking:
            missing.append("blocking_gaps")
        if missing:
            self._fail("RUNTIME_PRODUCTION_READINESS_GUARD", "production baseline is incomplete: " + ", ".join(sorted(set(missing))), self.plan_path, location="/", remediation="Complete the scope, specification, WBS, budget, schedule, risk, prototype decision, and review gates before production.", context={"missing": sorted(set(missing)), "blocking_gap_ids": blocking})

    def _check_task_start(self, state: dict[str, Any], occurred_at: str) -> None:
        plan = self._plan()
        if not state.get("task_graph_sha256") or not isinstance(state.get("task_states"), dict):
            self._fail("RUNTIME_PRODUCTION_TASK_GUARD", "production start requires a registered task graph", self.state_path, location="/task_graph_sha256", remediation="Register the task graph generated from the validated plan before starting production.")
        eligible = []
        for task in plan.get("tasks", []):
            if not isinstance(task, dict):
                continue
            runtime_task = state["task_states"].get(str(task.get("id")))
            if not isinstance(runtime_task, dict):
                continue
            try:
                if self.runtime._task_eligible(state, runtime_task, occurred_at=occurred_at, plan=plan):
                    eligible.append(str(task.get("id")))
            except (DiagnosticError, KeyError, TypeError, ValueError):
                continue
        if not eligible:
            self._fail("RUNTIME_PRODUCTION_TASK_GUARD", "no production task is eligible with current dependencies, resources, materials, approvals, and stopping limits", self.state_path, location="/task_states", remediation="Resolve every task gate or remain READY_FOR_PRODUCTION.")
        limits = load_config(self.repository, "stopping-policy.yaml")
        if len(state.get("task_states", {})) > int(limits.get("max_tasks", 0)):
            self._fail("RUNTIME_PRODUCTION_TASK_GUARD", "registered task count exceeds the configured stopping limit", self.state_path, remediation="Create a reviewed plan revision within the configured task limit.")

    def _latest(self, records: list[dict[str, Any]], identity: str) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for record in records:
            key = record.get(identity)
            if key is None:
                continue
            if key not in latest or int(record.get("revision", 0)) > int(latest[key].get("revision", 0)):
                latest[str(key)] = record
        return list(latest.values())

    def _check_ready_for_installation(self, state: dict[str, Any], occurred_at: str) -> None:
        outputs = self._latest(self._records(self.project_root / "05_execution/output-versions.yaml", "records", rule="RUNTIME_INSTALLATION_GUARD", required=True), "output_id")
        quality = {str(item.get("quality_id")): item for item in self._latest(self._records(self.project_root / "05_execution/quality-results.yaml", "records", rule="RUNTIME_INSTALLATION_GUARD", required=True), "quality_id")}
        plan = self._latest(self._records(self.project_root / "06_installation/installation-plan.yaml", "records", rule="RUNTIME_INSTALLATION_GUARD", required=True), "installation_plan_id")
        if not outputs or any(item.get("status") != "AVAILABLE" or not all(isinstance(quality.get(str(qid)), dict) and quality[str(qid)].get("status") == "PASS" and quality[str(qid)].get("external_validation_status") == "VERIFIED" for qid in item.get("quality_result_ids", [])) for item in outputs):
            self._fail("RUNTIME_INSTALLATION_GUARD", "mandatory outputs do not all have AVAILABLE status and PASS/VERIFIED quality", self.project_root / "05_execution/output-versions.yaml", remediation="Record each mandatory deliverable, its PASS quality result, and VERIFIED evidence before installation.")
        approved = [item for item in plan if item.get("status") == "APPROVED"]
        if not approved:
            self._fail("RUNTIME_INSTALLATION_GUARD", "no APPROVED installation plan exists", self.project_root / "06_installation/installation-plan.yaml", remediation="Create an installation plan with venue conditions, output references, safety approval, and an external effect target.")
        for item in approved:
            effect = item.get("external_effect_plan", {})
            if item.get("output_ids") and effect.get("execution_status") == "PLANNED" and item.get("venue_ref") and item.get("approval_ids"):
                latest = self.runtime._latest_approvals(state)
                for approval_id in item.get("approval_ids", []):
                    approval = latest.get(str(approval_id))
                    if not isinstance(approval, dict) or approval.get("decision") != "APPROVED":
                        self._fail("RUNTIME_INSTALLATION_APPROVAL_GUARD", f"installation approval {approval_id} is missing, revoked, or not APPROVED", self.state_path, location="/approvals", remediation="Record an unexpired approval for the exact installation target before installing.")
                    scope = approval.get("scope", {})
                    if scope.get("target_ref") != effect.get("target_ref") or scope.get("target_sha256") != effect.get("target_sha256") or scope.get("action") != effect.get("effect_type"):
                        self._fail("RUNTIME_INSTALLATION_APPROVAL_GUARD", f"installation approval {approval_id} does not match the exact external effect target", self.state_path, location="/approvals", remediation="Use an approval whose action, target_ref, and target_sha256 match the installation plan.")
                    try:
                        from .runtime import _timestamp

                        if _timestamp(str(approval.get("issued_at"))) > _timestamp(occurred_at) or _timestamp(str(approval.get("expires_at"))) <= _timestamp(occurred_at):
                            self._fail("RUNTIME_INSTALLATION_APPROVAL_GUARD", f"installation approval {approval_id} is not valid at installation time", self.state_path, location="/approvals", remediation="Use an approval whose validity window covers the installation event.")
                    except (TypeError, ValueError):
                        self._fail("RUNTIME_INSTALLATION_APPROVAL_GUARD", f"installation approval {approval_id} has invalid validity timestamps", self.state_path, location="/approvals", remediation="Record RFC 3339 issued_at and expires_at values for the approval.")
                if any(effect_record.get("status") in {"UNKNOWN", "STARTED"} for effect_record in state.get("effects", {}).values() if isinstance(effect_record, dict)):
                    self._fail("RUNTIME_INSTALLATION_GUARD", "installation cannot start while an external effect is UNKNOWN or in flight", self.state_path, location="/effects", remediation="Reconcile every existing effect before installation.")
                return
        self._fail("RUNTIME_INSTALLATION_GUARD", "approved installation plan lacks a concrete venue, output, approval, or planned external effect", self.project_root / "06_installation/installation-plan.yaml", remediation="Complete the installation plan fields and freeze the exact target hash.")

    def _check_installation_skip(self) -> None:
        plan = self._plan()
        skip = plan.get("installation_skip_decision")
        required = ("target", "reason", "basis", "authority", "target_hash")
        if not isinstance(skip, dict) or any(not isinstance(skip.get(key), str) or not skip.get(key).strip() for key in required) or not isinstance(skip.get("approval_required"), bool) or not str(skip.get("target_hash", "")).startswith("sha256:"):
            self._fail("RUNTIME_INSTALLATION_SKIP_GUARD", "PRODUCING -> VALIDATING requires a canonical structured installation skip decision", self.plan_path, location="/installation_skip_decision", remediation="Record target, rationale, basis, authority, exact target hash, and approval_required in the production plan.")

    def _check_installing_to_validating(self) -> None:
        results = self._latest(self._records(self.project_root / "06_installation/installation-results.yaml", "records", rule="RUNTIME_INSTALLATION_RESULT_GUARD", required=True), "installation_result_id")
        if not results or any(item.get("status") == "NOT_RUN" for item in results):
            self._fail("RUNTIME_INSTALLATION_RESULT_GUARD", "installation result is not terminal", self.project_root / "06_installation/installation-results.yaml", remediation="Record SUCCEEDED, FAILED, SKIPPED, or EXTERNAL_VALIDATION_REQUIRED before validation.")
        for result in results:
            if result.get("status") == "SUCCEEDED":
                if result.get("safety_check_status") != "PASS" or result.get("external_validation_status") == "PENDING" or not result.get("evidence_refs"):
                    self._fail("RUNTIME_INSTALLATION_RESULT_GUARD", "SUCCEEDED installation requires PASS safety and VERIFIED evidence", self.project_root / "06_installation/installation-results.yaml", remediation="Attach VERIFIED evidence and a PASS safety result, or record the installation as pending/failed.")
                self._verify_evidence(result.get("evidence_refs"), expected_targets={str(result.get("installation_result_id")), *[str(item) for item in result.get("output_ids", [])]}, rule="RUNTIME_INSTALLATION_RESULT_GUARD", path=self.project_root / "06_installation/installation-results.yaml")

    def _check_completion(self, to_state: str) -> None:
        result_path = self.project_root / "08_runtime/production-result.yaml"
        report_path = self.project_root / "08_runtime/completion-report.json"
        result = self._mapping(result_path, rule="RUNTIME_COMPLETION_GUARD")
        report = self._mapping(report_path, rule="RUNTIME_COMPLETION_GUARD")
        try:
            validate_result(result, repository=self.repository, result_path=result_path)
            validate_completion_report(report, repository=self.repository, report_path=report_path, expected_result=result)
        except DiagnosticError as exc:
            self._fail("RUNTIME_COMPLETION_GUARD", exc.finding.reason, Path(exc.finding.file or report_path), location=exc.finding.location, remediation=exc.finding.remediation, context={"cause": exc.finding.rule})
        if report.get("status") != "READY" or report.get("target_state") != to_state:
            self._fail("RUNTIME_COMPLETION_GUARD", "completion report is not READY for the requested terminal target", report_path, location="/target_state", remediation="Build a READY completion report for the exact terminal target before transitioning.")
        if report.get("result_sha256") != result.get("integrity", {}).get("content_sha256"):
            self._fail("RUNTIME_COMPLETION_GUARD", "completion report result hash does not match production-result.yaml", report_path, location="/result_sha256", remediation="Regenerate the completion report from the unchanged production result.")
        checks = report.get("checks")
        if not isinstance(checks, list) or not checks or any(check.get("status") != "PASS" for check in checks if isinstance(check, dict)) or any(not isinstance(check, dict) for check in checks):
            self._fail("RUNTIME_COMPLETION_GUARD", "all design-specification completion checks must be PASS", report_path, location="/checks", remediation="Resolve every failed or NOT_RUN completion check before accepting the terminal result.")
        result_gap_ids = {str(item.get("id")) for item in result.get("open_gaps", []) if isinstance(item, dict)}
        report_gap_ids = {str(item) for item in report.get("open_gap_ids", [])}
        if to_state == "COMPLETE" and result_gap_ids:
            self._fail("RUNTIME_COMPLETION_GUARD", "COMPLETE cannot contain open gaps", result_path, location="/open_gaps", remediation="Use COMPLETE_WITH_GAPS for non-blocking gaps or resolve them before completion.")
        if to_state == "COMPLETE_WITH_GAPS":
            if result_gap_ids != report_gap_ids or report.get("blocking_gap_ids"):
                self._fail("RUNTIME_COMPLETION_GAP_GUARD", "COMPLETE_WITH_GAPS gap IDs must match the result and contain no blocking gaps", report_path, location="/open_gap_ids", remediation="Regenerate the READY report with matching non-blocking gap IDs and resume conditions.", context={"result_gap_ids": sorted(result_gap_ids), "report_gap_ids": sorted(report_gap_ids), "blocking_gap_ids": report.get("blocking_gap_ids")})

    def _check_blocked(self, payload: dict[str, Any]) -> None:
        required = {"blocker", "impact", "owner", "resume_state", "resolution_condition", "source_refs"}
        missing = sorted(key for key in required if not payload.get(key))
        if missing:
            self._fail("RUNTIME_BLOCKER_GUARD", "active -> BLOCKED is missing: " + ", ".join(missing), self.state_path, location="/payload", remediation="Record blocker, impact, owner, resume_state, resolution_condition, and canonical source_refs.")
        resume = payload.get("resume_state")
        if resume not in self._ACTIVE_STATES:
            self._fail("RUNTIME_RESUME_STATE", "resume_state must name an active lifecycle state", self.state_path, location="/payload/resume_state", remediation="Use the exact active state to which the blocker can safely resume.")
        if not isinstance(payload.get("source_refs"), list) or not all(isinstance(item, str) and item.strip() for item in payload["source_refs"]):
            self._fail("RUNTIME_BLOCKER_GUARD", "blocker source_refs must contain canonical record IDs", self.state_path, location="/payload/source_refs", remediation="Reference the plan, approval, task, evidence, or change record that created the blocker.")

    def _check_resume(self, payload: dict[str, Any], to_state: str, event_sequence: int | None) -> None:
        evidence = payload.get("resolution_evidence")
        if not isinstance(evidence, list) or not evidence:
            self._fail("RUNTIME_RESUME_EVIDENCE", "BLOCKED resume requires non-empty resolution_evidence", self.state_path, location="/payload/resolution_evidence", remediation="Register VERIFIED evidence resolving every blocking condition.")
        resolved = self._verify_evidence(evidence, expected_targets={self.runtime._project_id()}, rule="RUNTIME_RESUME_EVIDENCE", path=self.state_path)
        blocked_event = next(
            (
                event
                for event in reversed(self.runtime._read_events())
                if event.get("type") == "PROJECT_STATE_TRANSITIONED"
                and isinstance(event.get("payload"), dict)
                and event["payload"].get("to_state") == "BLOCKED"
                and (event_sequence is None or int(event.get("sequence", 0)) < event_sequence)
            ),
            None,
        )
        source_refs = (blocked_event or {}).get("payload", {}).get("source_refs", []) if isinstance(blocked_event, dict) else []
        for source_ref in source_refs:
            if not any(source_ref in record.get("target_refs", []) or source_ref in record.get("trace_refs", []) for record in resolved):
                self._fail("RUNTIME_RESUME_EVIDENCE", f"resolution evidence does not cover blocker source {source_ref}", self.state_path, location="/payload/resolution_evidence", remediation="Attach VERIFIED evidence whose target_refs or trace_refs names every blocker source record.")
        if payload.get("resume_state") != to_state:
            self._fail("RUNTIME_RESUME_TARGET", "resume_state does not match the transition target", self.state_path, location="/payload/resume_state", remediation="Resume only to the state recorded when the blocker was created.")

    def _check_cancel(self, actor: dict[str, str], payload: dict[str, Any]) -> None:
        if actor.get("kind") != "HUMAN" or not payload.get("reason") or not payload.get("retention_decision") or not str(payload.get("target_hash", "")).startswith("sha256:"):
            self._fail("RUNTIME_CANCEL_GUARD", "CANCELLED requires HUMAN authority, reason, retention_decision, and target_hash", self.state_path, remediation="Record the human cancellation decision and exact retained target hash.")
        expected = canonical_sha256(self._manifest_immutable())
        if payload.get("target_hash") != expected:
            self._fail("RUNTIME_CANCEL_GUARD", "CANCELLED target_hash does not match the immutable project target", self.manifest_path, location="/target_hash", remediation="Bind cancellation to the current canonical project target hash.")

    def _check_reopen(self, actor: dict[str, str], payload: dict[str, Any]) -> None:
        if actor.get("kind") != "HUMAN" or not payload.get("reopen_reason") or not payload.get("change_request_id") or not str(payload.get("target_hash", "")).startswith("sha256:") or not str(payload.get("old_result_hash", "")).startswith("sha256:"):
            self._fail("RUNTIME_REOPEN_GUARD", "terminal reopen requires HUMAN authority, reason, change request, target hash, and old result hash", self.state_path, remediation="Create an authorized change request and bind the reopen to the prior result and exact target hash.")
        if payload.get("target_hash") != canonical_sha256(self._manifest_immutable()):
            self._fail("RUNTIME_REOPEN_GUARD", "reopen target_hash does not match the immutable project target", self.manifest_path, location="/target_hash", remediation="Bind the reopen to the current canonical project target hash.")
        result_path = self.project_root / "08_runtime/production-result.yaml"
        result = self._mapping(result_path, rule="RUNTIME_REOPEN_GUARD")
        if payload.get("old_result_hash") != result.get("integrity", {}).get("content_sha256"):
            self._fail("RUNTIME_REOPEN_GUARD", "old_result_hash does not match the retained production result", result_path, location="/integrity/content_sha256", remediation="Use the exact result hash being reopened; do not overwrite a terminal result silently.")
        change_path = self.project_root / "07_governance/change-requests.yaml"
        changes = self._records(change_path, "change_requests", rule="RUNTIME_REOPEN_GUARD", required=True)
        if not any(str(change.get("id")) == str(payload.get("change_request_id")) for change in changes):
            self._fail("RUNTIME_REOPEN_GUARD", "reopen change_request_id is not present in the canonical change request register", change_path, location="/change_requests", remediation="Record the change request in the canonical governance register before reopening.")

    def _evidence_paths(self, from_state: str, to_state: str) -> list[Path]:
        paths = [self.manifest_path, self.receipt_path, self.handoff_path]
        if from_state == "HANDOFF_VALIDATED" and to_state == "PLANNING":
            paths.extend([self.project_root / "00_handoff/source-bundle-manifest.yaml", self.project_root / "00_handoff/source-bundle/manifest.yaml"])
        if to_state in {"READY_FOR_PROTOTYPE", "PROTOTYPING", "REVIEWING", "READY_FOR_PRODUCTION", "PRODUCING", "VALIDATING"} or from_state in {"REVIEWING", "PRODUCING", "INSTALLING"}:
            paths.append(self.plan_path)
        if from_state in {"READY_FOR_PROTOTYPE", "PROTOTYPING", "REVIEWING"} or to_state in {"READY_FOR_PROTOTYPE", "PROTOTYPING", "REVIEWING", "READY_FOR_PRODUCTION"}:
            paths.append(self.control_path)
        # Evidence records are resolved again during replay.  The register is
        # append-only and legitimately grows after a transition, so its
        # projection is not part of the historical file-hash snapshot.
        if from_state == "PRODUCING" and to_state == "READY_FOR_INSTALLATION":
            paths.extend([self.project_root / "05_execution/output-versions.yaml", self.project_root / "05_execution/quality-results.yaml", self.project_root / "06_installation/installation-plan.yaml"])
        if from_state == "READY_FOR_INSTALLATION" and to_state == "INSTALLING":
            paths.extend([self.project_root / "06_installation/installation-plan.yaml", self.project_root / "07_governance/approval-register.yaml"])
        if from_state == "INSTALLING" and to_state == "VALIDATING":
            paths.append(self.project_root / "06_installation/installation-results.yaml")
        if to_state in self._TERMINAL_STATES:
            paths.extend([self.project_root / "08_runtime/production-result.yaml", self.project_root / "08_runtime/completion-report.json"])
        if from_state in self._TERMINAL_STATES and to_state == "PLANNING":
            paths.extend([self.project_root / "07_governance/change-requests.yaml", self.project_root / "08_runtime/production-result.yaml"])
        return paths

    def _hash_path(self, path: Path) -> str:
        if path == self.manifest_path:
            value = self._manifest_immutable()
            return canonical_sha256(value)
        if path.suffix == ".jsonl":
            return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        value = load_json(path) if path.suffix == ".json" else load_yaml(path)
        return canonical_sha256(value)

    def _historical_hashes(self, relative: str) -> set[str]:
        """Resolve the expected hash from an immutable handoff history snapshot."""

        candidates: list[Path] = []
        history_root = self.project_root / "00_handoff/history"
        if history_root.is_dir():
            relative_path = Path(relative)
            for entry in sorted(history_root.iterdir(), key=lambda path: path.name):
                if not entry.is_dir():
                    continue
                candidates.extend((entry / relative_path, entry / relative_path.name))
                if relative != "manifest.yaml":
                    candidates.append(entry / relative_path.parent.name / relative_path.name)
        relative_path = Path(relative)
        stage_history = self.project_root / relative_path.parent / "history"
        if stage_history.is_dir():
            for entry in sorted(stage_history.iterdir(), key=lambda path: path.name):
                candidates.append(entry / relative_path.name)
        hashes: set[str] = set()
        for path in candidates:
            if not path.is_file():
                continue
            try:
                if relative == "manifest.yaml":
                    value = load_yaml(path)
                    if isinstance(value, dict):
                        value = {key: item for key, item in value.items() if key != "state"}
                    hashes.add(canonical_sha256(value))
                    continue
                if relative.endswith(".jsonl"):
                    hashes.add(sha256_bytes(path.read_bytes()))
                    continue
                value = load_json(path) if path.suffix == ".json" else load_yaml(path)
                hashes.add(canonical_sha256(value))
            except (DiagnosticError, OSError, TypeError, ValueError):
                continue
        return hashes

    def evidence_hashes(self, from_state: str, to_state: str) -> dict[str, Any]:
        key = (from_state, to_state)
        paths = self._evidence_paths(from_state, to_state)
        signature = tuple(
            (str(path.relative_to(self.project_root)), *self._signature(path))
            for path in paths
            if path.is_file()
        )
        cached = self._evidence_cache.get(key)
        if cached is not None and cached[0] == signature:
            return deepcopy(cached[1])
        records: dict[str, str] = {}
        for path in paths:
            if path.is_file():
                records[str(path.relative_to(self.project_root))] = self._hash_path(path)
        evidence = {"schema_version": "1.0.0", "records": records, "records_sha256": canonical_sha256(records)}
        self._evidence_cache[key] = (signature, deepcopy(evidence))
        return evidence

    def _verify_recorded_evidence(self, from_state: str, to_state: str, recorded: Any) -> None:
        if not isinstance(recorded, dict) or recorded.get("schema_version") != "1.0.0" or not isinstance(recorded.get("records"), dict) or recorded.get("records_sha256") != canonical_sha256(recorded.get("records")):
            self._fail("RUNTIME_GUARD_EVIDENCE_HASH", "transition event is missing a valid guard evidence hash", self.log_path, location="/payload/guard_evidence", remediation="Append transitions through Runtime so the evaluated canonical record hashes are fixed in the event.")
        current = self.evidence_hashes(from_state, to_state)
        if recorded == current:
            return
        historical_records: dict[str, str] = {}
        for relative, expected_hash in recorded["records"].items():
            if current.get(relative) == expected_hash or expected_hash in self._historical_hashes(relative):
                historical_records[relative] = expected_hash
            else:
                self._fail("RUNTIME_GUARD_EVIDENCE_DIVERGENCE", "canonical records no longer match the guard evidence fixed in the transition event", self.log_path, location="/payload/guard_evidence", remediation="Restore the canonical record revision used by the event or reopen through a new authorized change request.", context={"recorded": recorded, "current": current})
        if historical_records != recorded["records"] or canonical_sha256(historical_records) != recorded["records_sha256"]:
            self._fail("RUNTIME_GUARD_EVIDENCE_DIVERGENCE", "historical canonical records do not match the guard evidence fixed in the transition event", self.log_path, location="/payload/guard_evidence", remediation="Restore the immutable stage snapshot used by the event.", context={"recorded": recorded, "historical": historical_records})

    def evaluate(self, from_state: str, to_state: str, payload: dict[str, Any], actor: dict[str, str], state: dict[str, Any], *, occurred_at: str, event_sequence: int | None = None, recorded_evidence: Any = None) -> dict[str, Any]:
        if from_state == "HANDOFF_VALIDATED" and to_state == "PLANNING":
            self._check_handoff_to_planning()
        elif from_state == "PLANNING" and to_state == "READY_FOR_PROTOTYPE":
            self._check_prototype_plan(require_control_ready=True)
        elif from_state == "READY_FOR_PROTOTYPE" and to_state == "PROTOTYPING":
            self._check_prototyping_start(state, occurred_at)
        elif from_state == "PROTOTYPING" and to_state == "REVIEWING":
            self._check_prototype_review(payload, state)
        elif from_state == "REVIEWING" and to_state == "PLANNING":
            self._check_review_to_planning(payload)
        elif from_state == "PLANNING" and to_state == "READY_FOR_PRODUCTION":
            self._check_production_readiness(require_review=False)
        elif from_state == "REVIEWING" and to_state == "READY_FOR_PRODUCTION":
            self._check_production_readiness(require_review=True)
        elif from_state == "READY_FOR_PRODUCTION" and to_state == "PRODUCING":
            self._check_task_start(state, occurred_at)
        elif from_state == "PRODUCING" and to_state == "READY_FOR_INSTALLATION":
            self._check_ready_for_installation(state, occurred_at)
        elif from_state == "PRODUCING" and to_state == "VALIDATING":
            self._check_installation_skip()
        elif from_state == "READY_FOR_INSTALLATION" and to_state == "INSTALLING":
            self._check_ready_for_installation(state, occurred_at)
        elif from_state == "INSTALLING" and to_state == "VALIDATING":
            self._check_installing_to_validating()
        elif to_state in self._TERMINAL_STATES:
            self._check_completion(to_state)
        elif to_state == "CANCELLED":
            self._check_cancel(actor, payload)
        elif to_state == "BLOCKED":
            self._check_blocked(payload)
        elif from_state == "BLOCKED":
            self._check_resume(payload, to_state, event_sequence)
        elif from_state in self._TERMINAL_STATES and to_state == "PLANNING":
            self._check_reopen(actor, payload)

        evidence = self.evidence_hashes(from_state, to_state)
        if recorded_evidence is not None:
            self._verify_recorded_evidence(from_state, to_state, recorded_evidence)
        return evidence
