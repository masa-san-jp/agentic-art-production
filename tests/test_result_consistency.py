from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.diagnostics import DiagnosticError
from tools.lib.execution import ExecutionManager
from tools.lib.result import build_result
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class ResultConsistencyContractTests(unittest.TestCase):
    def _project(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        output_root = Path(temporary.name) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / "production/smoke"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        self.assertEqual(ExecutionManager(project, ROOT).init()["revision"], 0)
        return temporary, project

    def test_complete_without_outputs_is_rejected_and_reported(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        with self.assertRaises(DiagnosticError) as context:
            build_result(project, ROOT, result_id="PR001", generated_at="2026-08-25T19:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567", target_state="COMPLETE")
        self.assertEqual(context.exception.finding.rule, "RESULT_COMPLETION_INCOMPLETE")
        report = load_yaml(project / "08_runtime/completion-report.json")
        self.assertEqual(report["status"], "REJECTED")
        self.assertIn("mandatory_deliverables_available", {check["rule"] for check in report["checks"] if check["status"] == "FAIL"})

    def test_complete_with_gaps_requires_a_non_empty_non_blocking_gap_set(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        plan_path = project / "03_plan/production-plan.yaml"
        plan = load_yaml(plan_path)
        plan["gaps"] = []
        dump_yaml(plan, plan_path)
        with self.assertRaises(DiagnosticError) as context:
            build_result(project, ROOT, result_id="PR001", generated_at="2026-08-25T19:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567", target_state="COMPLETE_WITH_GAPS")
        self.assertEqual(context.exception.finding.rule, "RESULT_COMPLETION_INCOMPLETE")
        report = load_yaml(project / "08_runtime/completion-report.json")
        self.assertEqual(report["open_gap_ids"], [])

    def test_blocked_candidate_has_blocking_gap_and_idempotent_report(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        first, first_idempotent = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-25T19:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567", target_state="BLOCKED")
        repeated, second_idempotent = build_result(project, ROOT, result_id="PR001", generated_at="2026-08-25T19:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567", target_state="BLOCKED")
        self.assertFalse(first_idempotent)
        self.assertTrue(second_idempotent)
        self.assertEqual(first, repeated)
        report = load_yaml(project / "08_runtime/completion-report.json")
        self.assertEqual(report["status"], "READY")
        self.assertTrue(report["blocking_gap_ids"])
        self.assertEqual(set(report["gap_sources"]), set(report["open_gap_ids"]))

    def test_same_result_id_cannot_change_target_state(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        build_result(project, ROOT, result_id="PR001", generated_at="2026-08-25T19:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567", target_state="BLOCKED")
        with self.assertRaises(DiagnosticError) as context:
            build_result(project, ROOT, result_id="PR001", generated_at="2026-08-25T19:00:00+09:00", production_commit="0123456789abcdef0123456789abcdef01234567", target_state="COMPLETE")
        self.assertEqual(context.exception.finding.rule, "RESULT_TARGET_STATE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
