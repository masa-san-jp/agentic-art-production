from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.canonical import canonical_sha256
from tools.lib.diagnostics import DiagnosticError
from tools.lib.evidence import EvidenceManager, resolve_evidence_refs
from tools.lib.execution import ExecutionManager
from tools.new_production import main as new_production_main
from tools.run_execution import main as run_execution_main
from tools.validate import validate_project


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/task-matrix"


class EvidenceContractTests(unittest.TestCase):
    def _project(self) -> tuple[tempfile.TemporaryDirectory, Path, EvidenceManager]:
        temporary = tempfile.TemporaryDirectory()
        output_root = Path(temporary.name) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / "production/smoke"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        self.assertEqual(ExecutionManager(project, ROOT).init()["revision"], 0)
        manager = EvidenceManager(project, ROOT)
        self.assertEqual(manager.init()["revision"], 0)
        return temporary, project, manager

    @staticmethod
    def _record(evidence_id: str = "EVD001", targets: list[str] | None = None, status: str = "VERIFIED") -> dict:
        targets = targets or ["AT001"]
        return {
            "schema_version": "1.0.0", "evidence_id": evidence_id, "revision": 1, "project_id": "production/smoke", "evidence_type": "ACCEPTANCE_TEST",
            "target_refs": targets, "uri": f"urn:test:evidence:{evidence_id}", "content_sha256": canonical_sha256({"id": evidence_id, "targets": targets}),
            "captured_at": "2026-08-25T10:00:00+09:00", "recorded_at": "2026-08-25T10:00:00+09:00",
            "recorded_by": {"kind": "AGENT", "id": "evidence-test"}, "verification_status": status, "verification_method": "test-metadata-review",
            "rights_status": "PROJECT_INTERNAL", "privacy_status": "PROJECT_INTERNAL", "limitations": "Metadata-only synthetic evidence.", "trace_refs": [evidence_id],
        }

    def test_append_replay_and_idempotency_project_validation(self) -> None:
        temporary, project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        record = self._record()
        first = manager.record_evidence(record, occurred_at=record["recorded_at"], actor_kind="AGENT", actor_id="evidence-test", idempotency_key="evidence/EVD001/1")
        second = manager.record_evidence(record, occurred_at=record["recorded_at"], actor_kind="AGENT", actor_id="evidence-test", idempotency_key="evidence/EVD001/1")
        self.assertEqual(first, second)
        self.assertEqual(manager.replay()["revision"], 1)
        self.assertEqual(validate_project(project, ROOT), [])
        self.assertEqual(len((project / "05_execution/evidence-log.jsonl").read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(manager.records()[0]["uri"], "urn:test:evidence:EVD001")

    def test_refs_are_exact_registered_verified_and_targeted(self) -> None:
        temporary, project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        with self.assertRaises(DiagnosticError) as old_shape:
            resolve_evidence_refs(project, ROOT, ["urn:test:evidence:EVD001"])
        self.assertEqual(old_shape.exception.finding.rule, "EVIDENCE_REFERENCE_SHAPE")
        with self.assertRaises(DiagnosticError) as missing:
            resolve_evidence_refs(project, ROOT, [{"evidence_id": "EVD001", "revision": 1}])
        self.assertEqual(missing.exception.finding.rule, "EVIDENCE_UNREGISTERED")
        manager.record_evidence(self._record(status="PENDING"), occurred_at="2026-08-25T10:00:00+09:00", actor_kind="AGENT", actor_id="evidence-test", idempotency_key="evidence/EVD001/1")
        with self.assertRaises(DiagnosticError) as pending:
            resolve_evidence_refs(project, ROOT, [{"evidence_id": "EVD001", "revision": 1}])
        self.assertEqual(pending.exception.finding.rule, "EVIDENCE_NOT_VERIFIED")
        with self.assertRaises(DiagnosticError) as mismatch:
            resolve_evidence_refs(project, ROOT, [{"evidence_id": "EVD001", "revision": 1}], expected_targets={"TK001"})
        self.assertEqual(mismatch.exception.finding.rule, "EVIDENCE_NOT_VERIFIED")

    def test_log_and_projection_tamper_are_detected(self) -> None:
        temporary, project, manager = self._project()
        self.addCleanup(temporary.cleanup)
        record = self._record()
        manager.record_evidence(record, occurred_at=record["recorded_at"], actor_kind="AGENT", actor_id="evidence-test", idempotency_key="evidence/EVD001/1")
        log_path = project / "05_execution/evidence-log.jsonl"
        original = log_path.read_text(encoding="utf-8")
        log_path.write_text(original.replace("EVD001", "EVD999", 1), encoding="utf-8")
        with self.assertRaises(DiagnosticError) as tamper:
            manager.replay()
        self.assertEqual(tamper.exception.finding.rule, "EVIDENCE_EVENT_HASH")
        log_path.write_text(original, encoding="utf-8")
        register = project / "05_execution/evidence-register.yaml"
        register.write_text(register.read_text(encoding="utf-8").replace("revision: 1", "revision: 9", 1), encoding="utf-8")
        with self.assertRaises(DiagnosticError) as divergence:
            manager.replay()
        self.assertEqual(divergence.exception.finding.rule, "EVIDENCE_STATE_DIVERGENCE")

    def test_record_evidence_requires_explicit_timestamp(self) -> None:
        temporary, project, _manager = self._project()
        self.addCleanup(temporary.cleanup)
        record_path = Path(temporary.name) / "evidence.json"
        record_path.write_text(json.dumps(self._record()), encoding="utf-8")
        with redirect_stdout(StringIO()):
            exit_code = run_execution_main(["--project-root", str(project), "record-evidence", "--record-json", str(record_path), "--actor-kind", "AGENT", "--actor-id", "evidence-test", "--idempotency-key", "evidence/cli/1", "--format", "json"])
        self.assertEqual(exit_code, 1)

    def test_execution_cli_init_materializes_evidence_register(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output_root = Path(temporary.name) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / "production/smoke"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        with redirect_stdout(StringIO()):
            exit_code = run_execution_main(["--project-root", str(project), "init", "--format", "json"])
        self.assertEqual(exit_code, 0)
        self.assertTrue((project / "05_execution/evidence-log.jsonl").is_file())
        self.assertTrue((project / "05_execution/evidence-register.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
