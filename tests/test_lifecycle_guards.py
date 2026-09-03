"""Focused acceptance tests for the fail-closed lifecycle guard boundary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.lib.canonical import event_sha256
from tools.lib.diagnostics import DiagnosticError
from tools.lib.runtime import Runtime
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class LifecycleGuardTests(unittest.TestCase):
    def _project(self, directory: str) -> Path:
        output_root = Path(directory) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        return output_root / "production/smoke"

    def test_failed_guard_does_not_change_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(directory)
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-25T10:00:00+09:00", actor_kind="SYSTEM", actor_id="test/guard")
            runtime.transition(
                to_state="PLANNING",
                occurred_at="2026-08-25T10:00:01+09:00",
                actor_kind="SYSTEM",
                actor_id="test/guard",
                idempotency_key="test/guard/planning",
                reason="Initialize planning for guard rejection.",
            )
            paths = [project / "08_runtime/run-log.jsonl", project / "08_runtime/production-state.json", project / "manifest.yaml"]
            before = [path.read_bytes() for path in paths]
            with self.assertRaises(DiagnosticError) as rejected:
                runtime.transition(
                    to_state="READY_FOR_PROTOTYPE",
                    occurred_at="2026-08-25T10:00:02+09:00",
                    actor_kind="SYSTEM",
                    actor_id="test/guard",
                    idempotency_key="test/guard/ready-prototype",
                    reason="The plan is intentionally absent.",
                )
            self.assertIn(rejected.exception.finding.rule, {"RUNTIME_PLAN_MISSING", "RUNTIME_PLAN_GUARD"})
            self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_blocked_requires_canonical_source_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(directory)
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-25T10:00:00+09:00", actor_kind="SYSTEM", actor_id="test/guard")
            runtime.transition(
                to_state="PLANNING",
                occurred_at="2026-08-25T10:00:01+09:00",
                actor_kind="SYSTEM",
                actor_id="test/guard",
                idempotency_key="test/guard/planning",
                reason="Initialize planning for guard rejection.",
            )
            with self.assertRaises(DiagnosticError) as rejected:
                runtime.transition(
                    to_state="BLOCKED",
                    occurred_at="2026-08-25T10:00:02+09:00",
                    actor_kind="SYSTEM",
                    actor_id="test/guard",
                    idempotency_key="test/guard/blocked-missing-source",
                    reason="Missing source refs must fail closed.",
                    payload={
                        "blocker": "AR001",
                        "impact": "The next stage cannot start.",
                        "owner": "production",
                        "resume_state": "PLANNING",
                        "resolution_condition": "Record the approval.",
                    },
                )
            self.assertEqual(rejected.exception.finding.rule, "RUNTIME_BLOCKER_GUARD")

    def test_replay_detects_tampered_guard_evidence_even_if_event_hash_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(directory)
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-25T10:00:00+09:00", actor_kind="SYSTEM", actor_id="test/guard")
            runtime.transition(
                to_state="PLANNING",
                occurred_at="2026-08-25T10:00:01+09:00",
                actor_kind="SYSTEM",
                actor_id="test/guard",
                idempotency_key="test/guard/planning",
                reason="Initialize planning for guard rejection.",
            )
            log_path = project / "08_runtime/run-log.jsonl"
            event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            event["payload"]["guard_evidence"]["records"]["manifest.yaml"] = "sha256:" + "0" * 64
            event["event_sha256"] = event_sha256(event)
            log_path.write_text(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            with self.assertRaises(DiagnosticError) as rejected:
                runtime.replay()
            self.assertEqual(rejected.exception.finding.rule, "RUNTIME_GUARD_EVIDENCE_HASH")
