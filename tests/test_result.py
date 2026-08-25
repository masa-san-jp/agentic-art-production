from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.build_prototype import main as build_prototype_main
from tools.export_result import export_result
from tools.lib.canonical import result_sha256
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


if __name__ == "__main__":
    unittest.main()
