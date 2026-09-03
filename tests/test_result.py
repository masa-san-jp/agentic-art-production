from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.build_prototype import main as build_prototype_main
from tools.export_result import export_result
from tools.lib.canonical import canonical_sha256, handoff_sha256, result_sha256
from tools.lib.diagnostics import DiagnosticError
from tools.lib.execution import ExecutionManager
from tools.lib.result import build_result, validate_result
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class ResultContractTests(unittest.TestCase):
    def _project(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        output_root = Path(temporary.name) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / "production/smoke"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        self.assertEqual(build_prototype_main(["--project-root", str(project)]), 0)
        self.assertEqual(ExecutionManager(project, ROOT).init()["revision"], 0)
        return temporary, project

    def test_result_is_deterministic_and_idempotent(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        first, first_idempotent = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        second, second_idempotent = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        self.assertFalse(first_idempotent)
        self.assertTrue(second_idempotent)
        self.assertEqual(first, second)
        self.assertEqual(first["test_results"][0]["result"], "NOT_RUN")
        self.assertEqual(first["outputs"], [])
        self.assertEqual(first["integrity"]["content_sha256"], result_sha256(first))
        validate_result(first, repository=ROOT)

    def test_export_manifest_is_minimal_deterministic_and_idempotent(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        result, _ = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        target = Path(temporary.name) / "feedback" / "smoke" / "PR001"
        exported, first_idempotent = export_result(project, target, ROOT)
        repeated, second_idempotent = export_result(project, target, ROOT)
        self.assertEqual(exported, repeated)
        self.assertFalse(first_idempotent)
        self.assertTrue(second_idempotent)
        self.assertEqual({path.name for path in target.iterdir()}, {"manifest.yaml", "production-result.yaml"})
        manifest = load_yaml(target / "manifest.yaml")
        self.assertEqual(manifest["result_id"], result["result_id"])
        self.assertEqual(manifest["result_sha256"], result["integrity"]["content_sha256"])
        with self.assertRaises(DiagnosticError) as boundary:
            export_result(project, project / "bundle", ROOT)
        self.assertEqual(boundary.exception.finding.rule, "RESULT_EXPORT_BOUNDARY")

    def test_result_security_and_same_id_drift_fail_closed(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        result, _ = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        unsafe = copy.deepcopy(result)
        unsafe["observations"] = [{"id": "OB001", "statement": "PRIVATE_RAW must not cross the result boundary.", "method": "test", "limitations": "test", "related_requirement_ids": []}]
        unsafe["integrity"] = {"content_sha256": result_sha256(unsafe)}
        with self.assertRaises(DiagnosticError) as security:
            validate_result(unsafe, repository=ROOT)
        self.assertEqual(security.exception.finding.rule, "PRIVATE_MARKER")

        drifted = load_yaml(project / "08_runtime/production-result.yaml")
        drifted["generated_at"] = "2026-08-12T18:01:00+09:00"
        drifted["integrity"] = {"content_sha256": result_sha256(drifted)}
        dump_yaml(drifted, project / "08_runtime/production-result.yaml")
        with self.assertRaises(DiagnosticError) as mismatch:
            build_result(project, ROOT, result_id="PR001", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(mismatch.exception.finding.rule, "RESULT_IDEMPOTENCY_MISMATCH")

    def test_explicit_viewer_response_is_preserved_as_aggregate_only(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        prototype_path = project / "04_prototype/prototype-control.yaml"
        prototype = load_yaml(prototype_path)
        prototype["test_results"] = [{
            "id": "PTR001", "run_id": "PRT001", "acceptance_test_id": "AT001", "result": "NOT_RUN", "executed_at": None,
            "external_validation_status": "NOT_REQUIRED", "evidence_refs": [], "conditions": "synthetic fixture", "deviations": [], "limitations": "synthetic fixture", "gap": {"id": "GP001", "statement": "Synthetic test has not run.", "impact": "No execution evidence exists.", "owner": "production", "blocking": False, "resolution_condition": "Run the synthetic test and record evidence."}, "trace_refs": [],
            "viewer_response": {
            "source_kind": "measured",
            "requirement_id": "RQ001",
            "presentation_mode": "gallery",
            "requirement_tags": ["clarity"],
            "sample_size": 5,
            "outcome_counts": {"pass": 4, "fail": 0, "unknown": 1},
            "evidence_refs": ["production-result:PR001#AT001"],
            "certainty": "medium",
            "consent_scope": "aggregate-only",
            },
        }]
        dump_yaml(prototype, prototype_path)
        result, _ = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(prototype["test_results"][0]["viewer_response"], result["test_results"][0]["viewer_response"])
        validate_result(result, repository=ROOT)

    def test_viewer_assessment_is_displayed_and_missing_blind_frame_review_blocks(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        assessment = {
            "schema_id": "viewer-response-assessment/v1", "assessment_id": "VRA-PRODUCTION-SMOKE-RQ001-GALLERY", "work_id": "production/smoke", "requirement_id": "RQ001", "presentation_mode": "gallery", "matching_tags": ["clarity"], "status": "UNKNOWN", "measured_sample_size": 1, "outcome_counts": {"pass": 1, "fail": 0, "unknown": 0}, "confidence_interval": None, "source_record_ids": ["VRR-001"], "external_evidence_refs": [], "conflict": False, "review_required": True, "review_kind": "BLIND_OR_FRAME", "source_commits": ["0123456789abcdef0123456789abcdef01234567"],
        }
        assessment_path = Path(temporary.name) / "assessment.json"
        assessment_path.write_text(json.dumps(assessment) + "\n", encoding="utf-8")
        self.assertEqual(build_plan_main(["--project-root", str(project), "--viewer-assessment", str(assessment_path), "--format", "json"]), 0)
        plan = load_yaml(project / "03_plan/production-plan.yaml")
        self.assertEqual("UNKNOWN", plan["viewer_response_assessments"][0]["status"])
        self.assertTrue(any(gap["rule"] == "PLANNING_VIEWER_REVIEW_REQUIRED" for gap in plan["gaps"]))

    def test_result_reuses_plan_normalization_for_legacy_handoff_gap(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        handoff_path = project / "00_handoff/production-handoff.yaml"
        handoff = load_yaml(handoff_path)
        handoff["open_gaps"] = [{
            "id": "GP001",
            "statement": "The synthetic venue is not fixed.",
            "impact": "The final placement cannot be confirmed.",
            "resolution_owner": "production",
            "blocking": False,
        }]
        handoff["integrity"] = {"content_sha256": handoff_sha256(handoff)}
        dump_yaml(handoff, handoff_path)
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)

        result, _ = build_result(
            project,
            ROOT,
            result_id="PR002",
            generated_at="2026-08-12T18:00:00+09:00",
            production_commit="0123456789abcdef0123456789abcdef01234567",
            target_state="BLOCKED",
        )
        handoff_gap_results = [gap for gap in result["open_gaps"] if gap["statement"] == "The synthetic venue is not fixed."]
        self.assertTrue(handoff_gap_results)
        self.assertTrue(all(gap["resolution_condition"] for gap in handoff_gap_results))

    def test_result_maps_complete_plan_risk_fields_and_rejects_missing_mitigation(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        plan_path = project / "03_plan/production-plan.yaml"
        plan = load_yaml(plan_path)
        plan["risks"].append({
            "id": "RK001",
            "title": "The synthetic review may miss a required viewpoint.",
            "source_handoff_gap_ids": [],
            "source_requirement_ids": ["RQ001"],
            "severity": "MAJOR",
            "likelihood": "UNKNOWN",
            "impact": "The acceptance decision could be incomplete.",
            "mitigation": "Run the required viewpoint review and record its evidence.",
            "owner_capability": "production-reviewer",
            "status": "OPEN",
            "blocking": True,
            "trace_refs": ["RK001"],
        })
        plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})}
        dump_yaml(plan, plan_path)
        result, _ = build_result(
            project,
            ROOT,
            result_id="PR002",
            generated_at="2026-08-12T18:00:00+09:00",
            production_commit="0123456789abcdef0123456789abcdef01234567",
            target_state="BLOCKED",
        )
        self.assertTrue(any(gap["statement"] == "The synthetic review may miss a required viewpoint." for gap in result["open_gaps"]))

        plan["risks"][-1].pop("mitigation")
        plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})}
        dump_yaml(plan, plan_path)
        with self.assertRaises(DiagnosticError) as context:
            build_result(
                project,
                ROOT,
                result_id="PR003",
                generated_at="2026-08-12T18:00:00+09:00",
                production_commit="0123456789abcdef0123456789abcdef01234567",
                target_state="BLOCKED",
            )
        self.assertEqual(context.exception.finding.rule, "RESULT_GAP_SOURCE_FIELDS")


if __name__ == "__main__":
    unittest.main()
