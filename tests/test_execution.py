from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.canonical import canonical_sha256
from tools.lib.diagnostics import DiagnosticError
from tools.lib.evidence import EvidenceManager
from tools.lib.execution import ExecutionManager
from tools.new_production import main as new_production_main
from tools.validate import validate_project


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"
MATRIX_FIXTURE = ROOT / "tests/fixtures/handoff/task-matrix"


class ExecutionContractTests(unittest.TestCase):
    def _project(self) -> tuple[tempfile.TemporaryDirectory, Path, ExecutionManager]:
        temporary = tempfile.TemporaryDirectory()
        output_root = Path(temporary.name) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(MATRIX_FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / "production/smoke"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        manager = ExecutionManager(project, ROOT)
        self.assertEqual(manager.init()["revision"], 0)
        evidence = EvidenceManager(project, ROOT)
        self.assertEqual(evidence.init()["revision"], 0)
        evidence.record_evidence({
            "schema_version": "1.0.0", "evidence_id": "EVD001", "revision": 1, "project_id": "production/smoke", "evidence_type": "QUALITY",
            "target_refs": ["QL001", "OUT001"], "uri": "urn:test:evidence:EVD001", "content_sha256": canonical_sha256({"evidence_id": "EVD001"}),
            "captured_at": "2026-08-12T12:00:00+09:00", "recorded_at": "2026-08-12T12:00:00+09:00", "recorded_by": {"kind": "AGENT", "id": "test-agent"},
            "verification_status": "VERIFIED", "verification_method": "synthetic-test-registration", "rights_status": "PROJECT_INTERNAL", "privacy_status": "PROJECT_INTERNAL",
            "limitations": "Synthetic metadata-only evidence.", "trace_refs": ["QL001"],
        }, occurred_at="2026-08-12T12:00:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="test/evidence/EVD001")
        self.assertEqual(validate_project(project, ROOT), [])
        return temporary, project, manager

    @staticmethod
    def _output(revision: int, status: str = "CANDIDATE", quality_ids: list[str] | None = None) -> dict:
        return {
            "schema_version": "1.0.0",
            "output_id": "OUT001",
            "revision": revision,
            "project_id": "production/smoke",
            "deliverable_id": "DL001",
            "version": f"1.0.{revision}",
            "asset_ref": {"asset_id": "AS001", "uri": "urn:asset:synthetic:OUT001", "version": f"1.0.{revision}", "sha256": "sha256:" + "1" * 64, "media_type": "model/gltf-binary", "rights_status": "PROJECT_INTERNAL"},
            "status": status,
            "source_task_ids": ["TK004"],
            "quality_result_ids": quality_ids or [],
            "created_at": "2026-08-12T12:00:00+09:00",
            "created_by": {"kind": "AGENT", "id": "test-agent"},
            "supersedes": {"output_id": "OUT001", "revision": 1} if revision > 1 else None,
        }

    @staticmethod
    def _quality(revision: int = 1, status: str = "PASS") -> dict:
        return {
            "schema_version": "1.0.0",
            "quality_id": "QL001",
            "revision": 1,
            "project_id": "production/smoke",
            "output_id": "OUT001",
            "output_revision": revision,
            "status": status,
            "dimensions": [{"id": "QD001", "criterion": "traceable synthetic output", "result": "PASS" if status == "PASS" else "NOT_RUN", "method": "fixture inspection"}],
            "evidence_refs": [{"evidence_id": "EVD001", "revision": 1}] if status == "PASS" else [],
            "external_validation_status": "NOT_REQUIRED",
            "executed_at": "2026-08-12T12:01:00+09:00" if status == "PASS" else None,
            "created_at": "2026-08-12T12:01:00+09:00",
            "created_by": {"kind": "AGENT", "id": "test-agent"},
        }

    def test_empty_register_and_external_pending_installation_are_replayable(self) -> None:
        temporary, project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        manager.record_output(self._output(1), occurred_at="2026-08-12T12:00:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="output/OUT001/1")
        manager.record_quality(self._quality(1, "PASS"), occurred_at="2026-08-12T12:01:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="quality/QL001/1")
        manager.record_output(self._output(2, "AVAILABLE", ["QL001"]), occurred_at="2026-08-12T12:02:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="output/OUT001/2")
        manager.record_installation_plan({
            "schema_version": "1.0.0", "installation_plan_id": "INP001", "revision": 1, "project_id": "production/smoke", "status": "PLANNED", "output_ids": ["OUT001"], "venue_ref": "urn:venue:synthetic:1", "constraints": ["external execution remains pending"], "approval_ids": [],
            "external_effect_plan": {"effect_type": "PHYSICAL_EXTERNAL", "target_ref": "urn:venue:synthetic:1", "target_sha256": "sha256:" + "2" * 64, "execution_status": "PLANNED"}, "created_at": "2026-08-12T12:03:00+09:00", "created_by": {"kind": "AGENT", "id": "test-agent"},
        }, occurred_at="2026-08-12T12:03:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="installation-plan/INP001/1")
        manager.record_installation_result({
            "schema_version": "1.0.0", "installation_result_id": "INR001", "revision": 1, "project_id": "production/smoke", "installation_plan_id": "INP001", "status": "EXTERNAL_VALIDATION_REQUIRED", "output_ids": ["OUT001"], "safety_check_status": "EXTERNAL_VALIDATION_REQUIRED", "evidence_refs": [], "deviations": ["No physical installation was performed by the test agent."], "started_at": None, "completed_at": None, "created_at": "2026-08-12T12:04:00+09:00", "created_by": {"kind": "AGENT", "id": "test-agent"},
        }, occurred_at="2026-08-12T12:04:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="installation-result/INR001/1")
        self.assertEqual(manager.replay()["revision"], 5)
        self.assertEqual(validate_project(project, ROOT), [])
        self.assertFalse((project / "05_execution").joinpath("OUT001.glb").exists())

    def test_available_output_and_successful_installation_fail_closed(self) -> None:
        temporary, _project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        manager.record_output(self._output(1), occurred_at="2026-08-12T12:00:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="output/OUT001/1")
        with self.assertRaises(DiagnosticError) as output_error:
            manager.record_output(self._output(2, "AVAILABLE"), occurred_at="2026-08-12T12:01:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="output/OUT001/2")
        self.assertEqual(output_error.exception.finding.rule, "EXECUTION_OUTPUT_GATE")

        manager.record_quality(self._quality(1, "PASS"), occurred_at="2026-08-12T12:01:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="quality/QL001/1")
        manager.record_output(self._output(2, "AVAILABLE", ["QL001"]), occurred_at="2026-08-12T12:02:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="output/OUT001/2")
        with self.assertRaises(DiagnosticError) as result_error:
            manager.record_installation_result({
                "schema_version": "1.0.0", "installation_result_id": "INR001", "revision": 1, "project_id": "production/smoke", "installation_plan_id": "INP001", "status": "SUCCEEDED", "output_ids": ["OUT001"], "safety_check_status": "NOT_RUN", "evidence_refs": [], "started_at": "2026-08-12T12:03:00+09:00", "completed_at": "2026-08-12T12:04:00+09:00", "created_at": "2026-08-12T12:04:00+09:00", "created_by": {"kind": "AGENT", "id": "test-agent"},
            }, occurred_at="2026-08-12T12:04:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="installation-result/INR001/1")
        self.assertEqual(result_error.exception.finding.rule, "EXECUTION_REFERENCE")

    def test_idempotency_and_projection_tamper_are_detected(self) -> None:
        temporary, project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        record = self._output(1)
        first = manager.record_output(record, occurred_at="2026-08-12T12:00:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="same-key")
        second = manager.record_output(record, occurred_at="2026-08-12T12:05:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="same-key")
        self.assertEqual(first, second)
        self.assertEqual(len((project / "05_execution/production-log.jsonl").read_text(encoding="utf-8").splitlines()), 1)
        changed = dict(record)
        changed["version"] = "9.9.9"
        with self.assertRaises(DiagnosticError) as mismatch:
            manager.record_output(changed, occurred_at="2026-08-12T12:06:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="same-key")
        self.assertEqual(mismatch.exception.finding.rule, "EXECUTION_IDEMPOTENCY_MISMATCH")
        projection = project / "05_execution/output-versions.yaml"
        value = projection.read_text(encoding="utf-8")
        projection.write_text(value.replace("revision: 1", "revision: 999", 1), encoding="utf-8")
        with self.assertRaises(DiagnosticError) as divergence:
            manager.replay()
        self.assertIn(divergence.exception.finding.rule, {"EXECUTION_STATE_DIVERGENCE", "SCHEMA_VALIDATION"})
