from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.canonical import canonical_sha256
from tools.lib.diagnostics import DiagnosticError
from tools.lib.evidence import EvidenceManager
from tools.lib.execution import ExecutionManager
from tools.lib.result import build_result
from tools.lib.yaml_io import load_yaml
from tools.new_production import main as new_production_main
from tools.run_execution import main as run_execution_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/task-matrix"


class ObservationContractTests(unittest.TestCase):
    def _project(self) -> tuple[tempfile.TemporaryDirectory, Path, ExecutionManager]:
        temporary = tempfile.TemporaryDirectory()
        output_root = Path(temporary.name) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / "production/smoke"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        manager = ExecutionManager(project, ROOT)
        self.assertEqual(manager.init()["revision"], 0)
        evidence = EvidenceManager(project, ROOT)
        self.assertEqual(evidence.init()["revision"], 0)
        evidence.record_evidence(
            {
                "schema_version": "1.0.0",
                "evidence_id": "EVD001",
                "revision": 1,
                "project_id": "production/smoke",
                "evidence_type": "TASK",
                "target_refs": ["TK004"],
                "uri": "urn:test:evidence:EVD001",
                "content_sha256": canonical_sha256({"evidence_id": "EVD001", "target": "TK004"}),
                "captured_at": "2026-08-12T12:00:00+09:00",
                "recorded_at": "2026-08-12T12:00:00+09:00",
                "recorded_by": {"kind": "AGENT", "id": "observation-test"},
                "verification_status": "VERIFIED",
                "verification_method": "synthetic-test-registration",
                "rights_status": "PROJECT_INTERNAL",
                "privacy_status": "PROJECT_INTERNAL",
                "limitations": "Synthetic metadata-only evidence.",
                "trace_refs": ["TK004"],
            },
            occurred_at="2026-08-12T12:00:00+09:00",
            actor_kind="AGENT",
            actor_id="observation-test",
            idempotency_key="observation-test/evidence/EVD001",
        )
        return temporary, project, manager

    @staticmethod
    def _observation(*, revision: int = 1, status: str = "ACTIVE", statement: str = "A synthetic observation.", source_refs: list[dict] | None = None) -> dict:
        return {
            "schema_version": "1.0.0",
            "observation_id": "OB001",
            "revision": revision,
            "project_id": "production/smoke",
            "statement": statement,
            "method": "synthetic-record-review",
            "limitations": "Synthetic metadata-only observation; no physical or audience work was performed.",
            "related_requirement_ids": ["RQ001"],
            "observed_at": "2026-08-12T12:01:00+09:00",
            "observer": {"kind": "AGENT", "id": "observation-test"},
            "source_refs": source_refs or [
                {"kind": "TASK", "id": "TK004", "revision": 1},
                {"evidence_id": "EVD001", "revision": 1},
            ],
            "privacy_status": "PROJECT_INTERNAL",
            "status": status,
            "supersedes": {"observation_id": "OB001", "revision": revision - 1} if revision > 1 else None,
        }

    def test_observation_is_lossless_in_projection_and_result(self) -> None:
        temporary, project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        record = self._observation()
        manager.record_observation(record, occurred_at="2026-08-12T12:01:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="observation/OB001/1")
        projection = load_yaml(project / "05_execution/observations.yaml")
        self.assertEqual(projection["records"], [record])
        result, _ = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(result["observations"], [{
            "id": "OB001",
            "statement": record["statement"],
            "method": record["method"],
            "limitations": record["limitations"],
            "related_requirement_ids": ["RQ001"],
        }])

    def test_zero_observations_and_retracted_latest_are_normal(self) -> None:
        temporary, project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        empty, _ = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(empty["observations"], [])
        manager.record_observation(self._observation(), occurred_at="2026-08-12T12:01:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="observation/OB001/1")
        retracted = self._observation(revision=2, status="RETRACTED")
        manager.record_observation(retracted, occurred_at="2026-08-12T12:02:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="observation/OB001/2")
        result, _ = build_result(project, ROOT, result_id="PR002", generated_at="2026-08-12T18:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(result["observations"], [])
        self.assertEqual(ExecutionManager(project, ROOT).replay()["records"]["OBSERVATION_RECORDED"], 2)

    def test_idempotency_revision_and_reference_guards_fail_closed(self) -> None:
        temporary, _project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        record = self._observation()
        first = manager.record_observation(record, occurred_at="2026-08-12T12:01:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="same-key")
        self.assertEqual(first, manager.record_observation(record, occurred_at="2026-08-12T12:09:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="same-key"))
        changed = copy.deepcopy(record)
        changed["statement"] = "Changed statement."
        with self.assertRaises(DiagnosticError) as key_error:
            manager.record_observation(changed, occurred_at="2026-08-12T12:02:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="same-key")
        self.assertEqual(key_error.exception.finding.rule, "EXECUTION_IDEMPOTENCY_MISMATCH")
        with self.assertRaises(DiagnosticError) as identity_error:
            manager.record_observation(changed, occurred_at="2026-08-12T12:02:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="different-key")
        self.assertEqual(identity_error.exception.finding.rule, "EXECUTION_RECORD_IMMUTABLE")
        with self.assertRaises(DiagnosticError) as gap_error:
            manager.record_observation(self._observation(revision=3), occurred_at="2026-08-12T12:03:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="observation/OB001/3")
        self.assertEqual(gap_error.exception.finding.rule, "OBSERVATION_REVISION_GAP")

        missing_requirement = self._observation()
        missing_requirement["observation_id"] = "OB002"
        missing_requirement["related_requirement_ids"] = ["RQ999"]
        with self.assertRaises(DiagnosticError) as requirement_error:
            manager.record_observation(missing_requirement, occurred_at="2026-08-12T12:04:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="observation/OB002/1")
        self.assertEqual(requirement_error.exception.finding.rule, "OBSERVATION_REQUIREMENT_REFERENCE")

        missing_source = self._observation()
        missing_source["observation_id"] = "OB003"
        missing_source["source_refs"] = [{"kind": "TASK", "id": "TK999", "revision": 1}]
        with self.assertRaises(DiagnosticError) as source_error:
            manager.record_observation(missing_source, occurred_at="2026-08-12T12:05:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="observation/OB003/1")
        self.assertEqual(source_error.exception.finding.rule, "OBSERVATION_SOURCE_REFERENCE")

        missing_evidence = self._observation()
        missing_evidence["observation_id"] = "OB004"
        missing_evidence["source_refs"] = [{"evidence_id": "EVD999", "revision": 1}]
        with self.assertRaises(DiagnosticError) as evidence_error:
            manager.record_observation(missing_evidence, occurred_at="2026-08-12T12:06:00+09:00", actor_kind="AGENT", actor_id="observation-test", idempotency_key="observation/OB004/1")
        self.assertEqual(evidence_error.exception.finding.rule, "EVIDENCE_UNREGISTERED")

    def test_cli_requires_explicit_observation_timestamp_and_key(self) -> None:
        temporary, project, _manager = self._project()
        self.addCleanup(temporary.cleanup)
        record_path = Path(temporary.name) / "observation.json"
        record_path.write_text(json.dumps(self._observation()), encoding="utf-8")
        self.assertNotEqual(run_execution_main(["--project-root", str(project), "record-observation", "--record-json", str(record_path)]), 0)


if __name__ == "__main__":
    unittest.main()
