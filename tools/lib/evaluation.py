"""Representative offline evaluation matrix for the production protocol."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from tools.build_plan import main as build_plan_main
from tools.build_prototype import main as build_prototype_main
from tools.export_result import export_result
from tools.lib.canonical import canonical_sha256, result_sha256
from tools.lib.diagnostics import DiagnosticError
from tools.lib.evidence import EvidenceManager
from tools.lib.execution import ExecutionManager
from tools.lib.result import build_result, validate_result
from tools.lib.runtime import Runtime
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.new_production import main as new_production_main
from tools.validate import validate_project, validate_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MINIMAL_HANDOFF = REPOSITORY_ROOT / "tests/fixtures/handoff/minimal"
TASK_MATRIX_HANDOFF = REPOSITORY_ROOT / "tests/fixtures/handoff/task-matrix"
GENERATED_AT = "2026-08-12T12:00:00+09:00"
PRODUCTION_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _quiet_call(function: Callable[[list[str]], int], arguments: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return function(arguments)


def _new_project(root: Path, *, build_prototype: bool) -> Path:
    output_root = root / "output"
    if _quiet_call(
        new_production_main,
        ["smoke", "--handoff", str(TASK_MATRIX_HANDOFF), "--output-root", str(output_root)],
    ) != 0:
        raise AssertionError("could not materialize the evaluation project")
    project = output_root / "production/smoke"
    if _quiet_call(build_plan_main, ["--project-root", str(project)]) != 0:
        raise AssertionError("could not build the evaluation plan")
    if build_prototype and _quiet_call(build_prototype_main, ["--project-root", str(project)]) != 0:
        raise AssertionError("could not build the evaluation prototype control")
    if ExecutionManager(project, REPOSITORY_ROOT).init().get("revision") != 0:
        raise AssertionError("evaluation execution register was not initialized at revision zero")
    if EvidenceManager(project, REPOSITORY_ROOT).init().get("revision") != 0:
        raise AssertionError("evaluation evidence register was not initialized at revision zero")
    return project


def _evidence_ref(evidence_id: str, revision: int = 1) -> dict[str, Any]:
    return {"evidence_id": evidence_id, "revision": revision}


def _record_synthetic_evidence(project: Path, *, evidence_id: str, evidence_type: str, targets: list[str], recorded_at: str) -> dict[str, Any]:
    record = {
        "schema_version": "1.0.0",
        "evidence_id": evidence_id,
        "revision": 1,
        "project_id": "production/smoke",
        "evidence_type": evidence_type,
        "target_refs": targets,
        "uri": f"urn:evaluation:evidence:{evidence_id}",
        "content_sha256": canonical_sha256({"evidence_id": evidence_id, "targets": targets}),
        "captured_at": recorded_at,
        "recorded_at": recorded_at,
        "recorded_by": {"kind": "SYSTEM", "id": "evaluation/evidence"},
        "verification_status": "VERIFIED",
        "verification_method": "synthetic-fixture-registration",
        "rights_status": "PROJECT_INTERNAL",
        "privacy_status": "PROJECT_INTERNAL",
        "limitations": "Synthetic metadata-only evidence; no physical or external effect was performed.",
        "trace_refs": [evidence_id],
    }
    EvidenceManager(project, REPOSITORY_ROOT).record_evidence(record, occurred_at=recorded_at, actor_kind="SYSTEM", actor_id="evaluation/evidence", idempotency_key=f"evaluation/evidence/{evidence_id}")
    return _evidence_ref(evidence_id)


def _runtime_project(root: Path) -> Path:
    project = _new_project(root, build_prototype=False)
    plan_path = project / "03_plan/production-plan.yaml"
    plan = load_yaml(plan_path)
    if not isinstance(plan, dict):
        raise AssertionError("evaluation plan is not a mapping")
    for resource in plan.get("resources", []):
        if isinstance(resource, dict):
            resource["availability"] = "AVAILABLE"
    for material in plan.get("materials", []):
        if isinstance(material, dict):
            material["status"] = "APPROVED"
    plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})}
    dump_yaml(plan, plan_path)
    return project


def _terminal_state(project: Path, terminal_state: str) -> dict[str, Any]:
    runtime = Runtime(project, REPOSITORY_ROOT)
    project_id = "production/smoke"
    prototype_evidence = _record_synthetic_evidence(project, evidence_id="EVD001", evidence_type="PROTOTYPE", targets=[project_id, "PRT001"], recorded_at="2026-08-12T12:00:03+09:00")
    completion_evidence = _record_synthetic_evidence(project, evidence_id="EVD002", evidence_type="ACCEPTANCE_TEST", targets=[project_id], recorded_at="2026-08-12T12:00:07+09:00")
    runtime.bootstrap(occurred_at=GENERATED_AT, actor_kind="SYSTEM", actor_id="evaluation/runtime")
    runtime.transition(
        to_state="READY_FOR_PROTOTYPE",
        occurred_at="2026-08-12T12:00:01+09:00",
        actor_kind="SYSTEM",
        actor_id="evaluation/runtime",
        idempotency_key="evaluation/state/ready-prototype",
        reason="Use an explicit synthetic prototype skip decision.",
        payload={"prototype_skip_decision": {"reason": "Synthetic evaluation does not perform physical work."}},
    )
    runtime.transition(
        to_state="PROTOTYPING",
        occurred_at="2026-08-12T12:00:02+09:00",
        actor_kind="SYSTEM",
        actor_id="evaluation/runtime",
        idempotency_key="evaluation/state/prototyping",
        reason="Enter the synthetic prototype stage.",
    )
    runtime.transition(
        to_state="REVIEWING",
        occurred_at="2026-08-12T12:00:03+09:00",
        actor_kind="SYSTEM",
        actor_id="evaluation/runtime",
        idempotency_key="evaluation/state/reviewing",
        reason="Use a synthetic external evidence reference for the gate.",
        payload={"external_evidence_refs": [prototype_evidence]},
    )
    runtime.transition(
        to_state="READY_FOR_PRODUCTION",
        occurred_at="2026-08-12T12:00:04+09:00",
        actor_kind="SYSTEM",
        actor_id="evaluation/runtime",
        idempotency_key="evaluation/state/ready-production",
        reason="Synthetic review completed without a physical effect.",
    )
    runtime.transition(
        to_state="PRODUCING",
        occurred_at="2026-08-12T12:00:05+09:00",
        actor_kind="SYSTEM",
        actor_id="evaluation/runtime",
        idempotency_key="evaluation/state/producing",
        reason="Enter synthetic production.",
    )
    runtime.transition(
        to_state="VALIDATING",
        occurred_at="2026-08-12T12:00:06+09:00",
        actor_kind="SYSTEM",
        actor_id="evaluation/runtime",
        idempotency_key="evaluation/state/validating",
        reason="Enter synthetic validation.",
    )
    completion_payload: dict[str, Any] = {"completion_evidence": [completion_evidence]}
    if terminal_state == "COMPLETE_WITH_GAPS":
        completion_payload["open_gap_ids"] = ["GP001"]
    if terminal_state == "BLOCKED":
        completion_payload.update(
            {
                "blocker": "Synthetic external validation is intentionally unavailable.",
                "impact": "The terminal production result cannot claim external completion.",
                "owner": "production",
                "resume_state": "VALIDATING",
                "resolution_condition": "Provide an authorized external validation record.",
            }
        )
    return runtime.transition(
        to_state=terminal_state,
        occurred_at="2026-08-12T12:00:07+09:00",
        actor_kind="SYSTEM",
        actor_id="evaluation/runtime",
        idempotency_key=f"evaluation/state/{terminal_state.lower()}",
        reason=f"Record the synthetic {terminal_state} terminal scenario.",
        payload=completion_payload,
    )


def _prepare_complete_fixture(project: Path, *, with_gap: bool) -> None:
    """Populate only synthetic canonical records needed for a COMPLETE candidate."""

    manifest_path = project / "manifest.yaml"
    manifest = load_yaml(manifest_path)
    manifest.update({"rights_status": "PROJECT_INTERNAL", "privacy_status": "PROJECT_INTERNAL", "publication_status": "UNPUBLISHED"})
    dump_yaml(manifest, manifest_path)

    plan_path = project / "03_plan/production-plan.yaml"
    plan = load_yaml(plan_path)
    plan["scope_baseline"]["status"] = "BASELINED"
    plan["scope_baseline"]["open_gap_ids"] = ["GP900"] if with_gap else []
    plan["gaps"] = [{
        "id": "GP900",
        "rule": "EVALUATION_NON_BLOCKING_AUDIT",
        "statement": "Synthetic audit note remains open after completion.",
        "impact": "The audit note does not prevent use of the available deliverable.",
        "owner": "production",
        "blocking": False,
        "resolution_condition": "Record the next audit observation in a subsequent production revision.",
        "source_refs": ["EVAL001"],
    }] if with_gap else []
    plan["schedule"]["gaps"] = []
    plan["schedule"]["baseline_status"] = "BASELINED"
    plan["budget"].update({
        "currency": "USD",
        "baseline_total": {"amount": "100", "currency": "USD"},
        "actual_total": {"amount": "100", "currency": "USD"},
        "variance": {"amount": "0", "currency": "USD"},
        "items": [{"id": "BI001", "category": "SYNTHETIC", "description": "Synthetic production fixture", "amount": {"amount": "100", "currency": "USD"}, "basis": "evaluation", "confidence": "HIGH", "status": "ACTUAL", "trace_refs": ["EVAL001"]}],
        "gaps": [],
        "status": "ACTUAL",
    })
    plan["acceptance_tests"][0]["result"] = "PASS"
    for specification in plan.get("technical_specifications", []):
        specification["status"] = "BASELINED"
        specification["measurement_status"] = "PASS"
    plan["installation_skip_decision"] = {"target": "installation", "reason": "Synthetic evaluation has no physical installation scope.", "basis": "The accepted fixture stops at metadata-only validation.", "approval_required": False}
    plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})}
    dump_yaml(plan, plan_path)

    test_evidence = _record_synthetic_evidence(project, evidence_id="EVD003", evidence_type="PROTOTYPE", targets=["PRT001", "PTR001", "AT001"], recorded_at="2026-08-12T12:00:08+09:00")
    control_path = project / "04_prototype/prototype-control.yaml"
    control = load_yaml(control_path)
    control["state"] = "READY_FOR_PROTOTYPE"
    control["plan_ref"] = {"id": plan["plan_id"], "revision": plan["plan_revision"], "content_sha256": plan["integrity"]["content_sha256"]}
    if not control.get("test_results"):
        control["test_results"] = [{
            "id": "PTR001", "run_id": "PRT001", "acceptance_test_id": "AT001", "result": "PASS", "executed_at": "2026-08-12T12:00:08+09:00",
            "external_validation_status": "VERIFIED", "evidence_refs": [test_evidence], "conditions": "Synthetic acceptance preconditions are satisfied.", "deviations": [],
            "limitations": "Synthetic metadata-only prototype evidence.", "trace_refs": ["HO002", "PH001", "RQ001", "PP001", "PRT001", "PTR001", "AT001"],
        }]
    for run in control.get("runs", []):
        run["status"] = "COMPLETE"
        run["external_validation_status"] = "VERIFIED"
        run["evidence_refs"] = [test_evidence]
        run["started_at"] = "2026-08-12T12:00:02+09:00"
        run["finished_at"] = "2026-08-12T12:00:08+09:00"
        run["test_result_ids"] = [test["id"] for test in control.get("test_results", []) if test.get("run_id") == run.get("id")]
        run.pop("gap", None)
    for test in control.get("test_results", []):
        test["result"] = "PASS"
        test["executed_at"] = "2026-08-12T12:00:08+09:00"
        test["external_validation_status"] = "VERIFIED"
        test["evidence_refs"] = [test_evidence]
        test.pop("gap", None)
    for review in control.get("reviews", []):
        review["status"] = "COMPLETE"
        review["overall_result"] = "PASS"
        review["external_validation_status"] = "VERIFIED"
        for assessment in review.get("assessments", []):
            assessment["result"] = "PASS"
    for decision in control.get("iteration_decisions", []):
        decision.update({"decision": "PROCEED", "status": "APPROVED", "authority": "HUMAN"})
    control["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in control.items() if key != "integrity"})}
    dump_yaml(control, control_path)

    output_evidence = _record_synthetic_evidence(project, evidence_id="EVD004", evidence_type="QUALITY", targets=["QL001", "OUT001"], recorded_at="2026-08-12T12:00:09+09:00")
    manager = ExecutionManager(project, REPOSITORY_ROOT)
    output = {
        "schema_version": "1.0.0", "output_id": "OUT001", "revision": 1, "project_id": "production/smoke", "deliverable_id": "DL001", "version": "1.0.1",
        "asset_ref": {"asset_id": "AS001", "uri": "urn:evaluation:asset:OUT001", "version": "1.0.1", "sha256": "sha256:" + "1" * 64, "media_type": "application/octet-stream", "rights_status": "PROJECT_INTERNAL"},
        "status": "CANDIDATE", "source_task_ids": ["TK004"], "quality_result_ids": [], "created_at": "2026-08-12T12:00:09+09:00", "created_by": {"kind": "SYSTEM", "id": "evaluation/result"}, "supersedes": None,
    }
    manager.record_output(output, occurred_at="2026-08-12T12:00:09+09:00", actor_kind="SYSTEM", actor_id="evaluation/result", idempotency_key="evaluation/output/1")
    quality = {
        "schema_version": "1.0.0", "quality_id": "QL001", "revision": 1, "project_id": "production/smoke", "output_id": "OUT001", "output_revision": 1, "status": "PASS",
        "dimensions": [{"id": "QD001", "criterion": "synthetic output traceability", "result": "PASS", "method": "fixture inspection"}], "evidence_refs": [output_evidence], "external_validation_status": "VERIFIED", "executed_at": "2026-08-12T12:00:10+09:00", "created_at": "2026-08-12T12:00:10+09:00", "created_by": {"kind": "SYSTEM", "id": "evaluation/result"},
    }
    manager.record_quality(quality, occurred_at="2026-08-12T12:00:10+09:00", actor_kind="SYSTEM", actor_id="evaluation/result", idempotency_key="evaluation/quality/1")
    output["revision"] = 2
    output["version"] = "1.0.2"
    output["asset_ref"]["version"] = "1.0.2"
    output["status"] = "AVAILABLE"
    output["quality_result_ids"] = ["QL001"]
    output["supersedes"] = {"output_id": "OUT001", "revision": 1}
    manager.record_output(output, occurred_at="2026-08-12T12:00:11+09:00", actor_kind="SYSTEM", actor_id="evaluation/result", idempotency_key="evaluation/output/2")


def _build_exported_result(project: Path, target: Path, result_id: str, *, target_state: str = "BLOCKED") -> dict[str, Any]:
    ExecutionManager(project, REPOSITORY_ROOT).record_observation(
        {
            "schema_version": "1.0.0",
            "observation_id": "OB001",
            "revision": 1,
            "project_id": "production/smoke",
            "statement": "Synthetic evaluation observation remains traceable to the explicit production task.",
            "method": "synthetic-plan-record-review",
            "limitations": "Synthetic metadata-only observation; no physical or audience work was performed.",
            "related_requirement_ids": ["RQ001"],
            "observed_at": GENERATED_AT,
            "observer": {"kind": "SYSTEM", "id": "evaluation/observation"},
            "source_refs": [{"kind": "TASK", "id": "TK004", "revision": 1}],
            "privacy_status": "PROJECT_INTERNAL",
            "status": "ACTIVE",
            "supersedes": None,
        },
        occurred_at=GENERATED_AT,
        actor_kind="SYSTEM",
        actor_id="evaluation/observation",
        idempotency_key="evaluation/observation/OB001/1",
    )
    result, first_idempotent = build_result(
        project,
        REPOSITORY_ROOT,
        result_id=result_id,
        generated_at=GENERATED_AT,
        production_commit=PRODUCTION_COMMIT,
        target_state=target_state,
    )
    if first_idempotent:
        raise AssertionError("the first evaluation result build unexpectedly reported idempotent")
    repeated, second_idempotent = build_result(
        project,
        REPOSITORY_ROOT,
        result_id=result_id,
        generated_at=GENERATED_AT,
        production_commit=PRODUCTION_COMMIT,
        target_state=target_state,
    )
    if not second_idempotent or repeated != result:
        raise AssertionError("repeated evaluation result build was not deterministic")
    exported, first_export_idempotent = export_result(project, target, REPOSITORY_ROOT)
    repeated_export, second_export_idempotent = export_result(project, target, REPOSITORY_ROOT)
    if exported != repeated_export or first_export_idempotent or not second_export_idempotent:
        raise AssertionError("repeated evaluation result export was not idempotent")
    if {path.name for path in target.iterdir()} != {"manifest.yaml", "production-result.yaml"}:
        raise AssertionError("evaluation result bundle contains an undeclared file")
    return result


def _expect_rule(function: Callable[[], Any], rule: str) -> None:
    try:
        function()
    except DiagnosticError as exc:
        if exc.finding.rule != rule:
            raise AssertionError(f"expected {rule}, got {exc.finding.rule}") from exc
        return
    raise AssertionError(f"expected DiagnosticError {rule}")


def _evaluate_resume_and_effect_idempotency(root: Path) -> dict[str, Any]:
    project = _runtime_project(root)
    effect_evidence = _record_synthetic_evidence(project, evidence_id="EVD001", evidence_type="EFFECT", targets=["evaluation/effect/1", "TK004"], recorded_at="2026-08-12T12:12:03+09:00")
    task_evidence = _record_synthetic_evidence(project, evidence_id="EVD002", evidence_type="TASK", targets=["TK004"], recorded_at="2026-08-12T12:12:05+09:00")
    runtime = Runtime(project, REPOSITORY_ROOT)
    runtime.bootstrap(occurred_at=GENERATED_AT, actor_kind="SYSTEM", actor_id="evaluation/runtime")
    runtime.initialize_task_graph(occurred_at="2026-08-12T12:00:01+09:00", actor_kind="SYSTEM", actor_id="evaluation/runtime")
    if runtime.next_task(occurred_at="2026-08-12T12:00:02+09:00") != "TK004":
        raise AssertionError("deterministic eligible task was not TK004")
    runtime.claim_task(task_id="TK004", occurred_at="2026-08-12T12:00:03+09:00", actor_id="worker/a", lease_token="lease-a", expires_at="2026-08-12T12:05:03+09:00", idempotency_key="evaluation/claim/1")
    resumed = runtime.claim_task(task_id="TK004", occurred_at="2026-08-12T12:10:00+09:00", actor_id="worker/b", lease_token="lease-b", expires_at="2026-08-12T12:15:00+09:00", idempotency_key="evaluation/claim/2")
    if resumed["task_states"]["TK004"]["attempt"] != 2:
        raise AssertionError("expired lease did not resume at attempt two")
    runtime.retry_task(task_id="TK004", occurred_at="2026-08-12T12:11:00+09:00", actor_id="worker/b", lease_token="lease-b", retry_after="2026-08-12T12:12:00+09:00", reason="synthetic transient interruption", idempotency_key="evaluation/retry/1")
    runtime.claim_task(task_id="TK004", occurred_at="2026-08-12T12:12:01+09:00", actor_id="worker/c", lease_token="lease-c", expires_at="2026-08-12T12:17:00+09:00", idempotency_key="evaluation/claim/3")
    runtime.start_effect(task_id="TK004", occurred_at="2026-08-12T12:12:02+09:00", actor_id="worker/c", lease_token="lease-c", effect_key="evaluation/effect/1", target_ref="urn:evaluation:task:TK004", target_sha256="sha256:" + "1" * 64, idempotency_key="evaluation/effect/start")
    final_effect = runtime.complete_effect(effect_key="evaluation/effect/1", task_id="TK004", occurred_at="2026-08-12T12:12:03+09:00", actor_id="worker/c", lease_token="lease-c", status="SUCCEEDED", evidence_refs=[effect_evidence], idempotency_key="evaluation/effect/complete")
    duplicate = runtime.start_effect(task_id="TK004", occurred_at="2026-08-12T12:12:04+09:00", actor_id="worker/c", lease_token="lease-c", effect_key="evaluation/effect/1", target_ref="urn:evaluation:task:TK004", target_sha256="sha256:" + "1" * 64, idempotency_key="evaluation/effect/retry")
    if duplicate != final_effect:
        raise AssertionError("duplicate succeeded effect was not an idempotent no-op")
    final = runtime.complete_task(task_id="TK004", occurred_at="2026-08-12T12:12:05+09:00", actor_id="worker/c", lease_token="lease-c", evidence_refs=[task_evidence], idempotency_key="evaluation/task/complete")
    if runtime.replay() != final or final["task_states"]["TK004"]["status"] != "DONE":
        raise AssertionError("resume scenario did not replay to the terminal task state")
    return {"status": "PASS", "terminal_task": "TK004", "attempt": final["task_states"]["TK004"]["attempt"]}


def _evaluate_approval_gates(root: Path) -> dict[str, Any]:
    project = _runtime_project(root)
    runtime = Runtime(project, REPOSITORY_ROOT)
    runtime.bootstrap(occurred_at=GENERATED_AT, actor_kind="SYSTEM", actor_id="evaluation/runtime")
    runtime.initialize_task_graph(occurred_at="2026-08-12T12:00:01+09:00", actor_kind="SYSTEM", actor_id="evaluation/runtime")
    plan = load_yaml(project / "03_plan/production-plan.yaml")
    requirement = plan["approval_register"]["requirements"][0]
    approval = {
        "approval_id": "AP001",
        "revision": 1,
        "decision": "APPROVED",
        "scope": {"action": requirement["action"], "target_ref": requirement["target_ref"], "target_sha256": requirement["target_sha256"]},
        "approver": {"id": "human/evaluation", "authority": "HUMAN"},
        "issued_at": "2026-08-12T12:00:02+09:00",
        "expires_at": "2026-08-12T12:05:00+09:00",
        "constraints": [],
    }
    runtime.record_approval(approval=approval, occurred_at="2026-08-12T12:00:02+09:00", actor_kind="HUMAN", actor_id="human/evaluation", idempotency_key="evaluation/approval/1")
    runtime.claim_task(task_id="TK001", occurred_at="2026-08-12T12:01:00+09:00", actor_id="worker/approval", lease_token="lease-approval", expires_at="2026-08-12T12:20:00+09:00", idempotency_key="evaluation/approval-claim")
    _expect_rule(lambda: runtime.start_effect(task_id="TK001", occurred_at="2026-08-12T12:06:00+09:00", actor_id="worker/approval", lease_token="lease-approval", effect_key="evaluation/approval/expired", target_ref=requirement["target_ref"], target_sha256=requirement["target_sha256"], idempotency_key="evaluation/approval/expired"), "RUNTIME_APPROVAL_EXPIRED")
    mismatched = deepcopy(approval)
    mismatched["revision"] = 2
    mismatched["expires_at"] = "2026-08-12T13:00:00+09:00"
    mismatched["scope"] = dict(approval["scope"])
    mismatched["scope"]["target_sha256"] = "sha256:" + "3" * 64
    runtime.record_approval(approval=mismatched, occurred_at="2026-08-12T12:06:01+09:00", actor_kind="HUMAN", actor_id="human/evaluation", idempotency_key="evaluation/approval/2")
    _expect_rule(lambda: runtime.start_effect(task_id="TK001", occurred_at="2026-08-12T12:06:02+09:00", actor_id="worker/approval", lease_token="lease-approval", effect_key="evaluation/approval/hash", target_ref=requirement["target_ref"], target_sha256=requirement["target_sha256"], idempotency_key="evaluation/approval/hash"), "RUNTIME_APPROVAL_HASH_MISMATCH")
    revoked = deepcopy(approval)
    revoked["revision"] = 3
    revoked["expires_at"] = "2026-08-12T13:00:00+09:00"
    revoked["decision"] = "REVOKED"
    runtime.record_approval(approval=revoked, occurred_at="2026-08-12T12:06:03+09:00", actor_kind="HUMAN", actor_id="human/evaluation", idempotency_key="evaluation/approval/3")
    _expect_rule(lambda: runtime.start_effect(task_id="TK001", occurred_at="2026-08-12T12:06:04+09:00", actor_id="worker/approval", lease_token="lease-approval", effect_key="evaluation/approval/revoked", target_ref=requirement["target_ref"], target_sha256=requirement["target_sha256"], idempotency_key="evaluation/approval/revoked"), "RUNTIME_APPROVAL_REVOKED")
    wildcard = deepcopy(approval)
    wildcard["approval_id"] = "AP009"
    wildcard["scope"] = {"action": "PHYSICAL_EXTERNAL", "target_ref": "urn:evaluation:task:*", "target_sha256": "sha256:" + "9" * 64}
    _expect_rule(lambda: runtime.record_approval(approval=wildcard, occurred_at="2026-08-12T12:06:05+09:00", actor_kind="HUMAN", actor_id="human/evaluation", idempotency_key="evaluation/approval/wildcard"), "RUNTIME_APPROVAL_WILDCARD")
    return {"status": "PASS", "rules": ["RUNTIME_APPROVAL_EXPIRED", "RUNTIME_APPROVAL_HASH_MISMATCH", "RUNTIME_APPROVAL_REVOKED", "RUNTIME_APPROVAL_WILDCARD"]}


def _evaluate_security_and_chaos(root: Path) -> dict[str, Any]:
    project = _new_project(root, build_prototype=True)
    result = _build_exported_result(project, root / "feedback/security", "PR001")
    unsafe = deepcopy(result)
    unsafe["observations"][0]["statement"] = "PRIVATE_RAW must not cross the result boundary."
    unsafe["integrity"] = {"content_sha256": result_sha256(unsafe)}
    _expect_rule(lambda: validate_result(unsafe, repository=REPOSITORY_ROOT), "PRIVATE_MARKER")

    runtime = Runtime(project, REPOSITORY_ROOT)
    log_path = project / "08_runtime/run-log.jsonl"
    original_log = log_path.read_bytes()
    log_path.write_bytes(original_log + b'{"partial":true')
    _expect_rule(runtime.replay, "RUNTIME_PARTIAL_LINE")
    log_path.write_bytes(original_log)
    state_path = project / "08_runtime/production-state.json"
    original_state = state_path.read_bytes()
    state_text = original_state.decode("utf-8")
    state_path.write_text(state_text.replace('"state": "HANDOFF_VALIDATED"', '"state": "PLANNING"', 1), encoding="utf-8")
    _expect_rule(runtime.replay, "RUNTIME_STATE_DIVERGENCE")
    state_path.write_bytes(original_state)
    tampered = root / "feedback/security" / "unexpected.txt"
    tampered.write_text("undeclared", encoding="utf-8")
    _expect_rule(lambda: export_result(project, root / "feedback/security", REPOSITORY_ROOT), "RESULT_EXPORT_IDEMPOTENCY_MISMATCH")
    tampered.unlink()
    return {"status": "PASS", "rules": ["PRIVATE_MARKER", "RUNTIME_PARTIAL_LINE", "RUNTIME_STATE_DIVERGENCE", "RESULT_EXPORT_IDEMPOTENCY_MISMATCH"]}


def run_evaluation() -> dict[str, Any]:
    repository_findings = validate_repository(REPOSITORY_ROOT)
    if repository_findings:
        raise AssertionError(f"repository contract validation failed: {repository_findings[0].render()}")
    with TemporaryDirectory(prefix="agentic-art-production-eval-") as temporary:
        root = Path(temporary)
        e2e_project = _new_project(root / "e2e", build_prototype=True)
        if validate_project(e2e_project, REPOSITORY_ROOT):
            raise AssertionError("offline E2E project validation failed")
        e2e_result = _build_exported_result(e2e_project, root / "feedback/e2e", "PR001")

        scenarios: list[dict[str, Any]] = []
        for index, terminal_state in enumerate(("COMPLETE", "COMPLETE_WITH_GAPS", "BLOCKED"), start=1):
            scenario_root = root / terminal_state.lower()
            project = _new_project(scenario_root, build_prototype=terminal_state != "BLOCKED")
            if terminal_state in {"COMPLETE", "COMPLETE_WITH_GAPS"}:
                _prepare_complete_fixture(project, with_gap=terminal_state == "COMPLETE_WITH_GAPS")
            state = _terminal_state(project, terminal_state)
            if state.get("state") != terminal_state or validate_project(project, REPOSITORY_ROOT):
                raise AssertionError(f"terminal scenario {terminal_state} did not validate")
            result = _build_exported_result(project, root / f"feedback/{terminal_state.lower()}", f"PR{index:03d}", target_state=terminal_state)
            scenarios.append({"id": terminal_state, "state": state["state"], "result_sha256": result["integrity"]["content_sha256"], "test_result_count": len(result["test_results"])})

        resume = _evaluate_resume_and_effect_idempotency(root / "resume")
        approval = _evaluate_approval_gates(root / "approval")
        security_chaos = _evaluate_security_and_chaos(root / "security-chaos")
        checks = [
            {"id": "OFFLINE_E2E", "status": "PASS", "evidence": e2e_result["integrity"]["content_sha256"]},
            {"id": "CONTRACT_TRACEABILITY", "status": "PASS", "evidence": ["HO002", "PH001", "RQ001", "AT001", "OB001"]},
            {"id": "DETERMINISM_IDEMPOTENCY", "status": "PASS", "evidence": [scenario["result_sha256"] for scenario in scenarios]},
            {"id": "RESUME_EFFECT_IDEMPOTENCY", "status": resume["status"], "evidence": resume},
            {"id": "APPROVAL_GATES", "status": approval["status"], "evidence": approval["rules"]},
            {"id": "SECURITY_CHAOS_RECOVERY", "status": security_chaos["status"], "evidence": security_chaos["rules"]},
        ]
        return {"evaluation_schema_version": "1.0.0", "generated_at": GENERATED_AT, "production_commit": PRODUCTION_COMMIT, "status": "PASS", "scenarios": scenarios, "checks": checks}


def render_evaluation(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    lines = [f"EVAL-001: {report['status']}", f"scenarios: {len(report['scenarios'])}", f"checks: {len(report['checks'])}"]
    lines.extend(f"- {check['id']}: {check['status']}" for check in report["checks"])
    lines.extend(f"- {scenario['id']}: {scenario['state']} {scenario['result_sha256']}" for scenario in report["scenarios"])
    return "\n".join(lines)
