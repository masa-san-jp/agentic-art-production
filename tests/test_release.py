from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from tools.lib.release import run_release_gate
from tools.run_release_gate import _write_evidence


ROOT = Path(__file__).resolve().parents[1]


class ReleaseGateContractTests(unittest.TestCase):
    def _runner(self, *, failed_command: str | None = None):
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            calls.append(command)
            if command[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
            if command[:2] == ["git", "status"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if failed_command and failed_command in command:
                return subprocess.CompletedProcess(command, 1, "", "synthetic failure")
            if any(part.endswith("validate.py") for part in command):
                return subprocess.CompletedProcess(command, 0, "[]\n", "")
            if any(part.endswith("run_evaluation.py") for part in command):
                return subprocess.CompletedProcess(command, 0, json.dumps({"status": "PASS"}), "")
            return subprocess.CompletedProcess(command, 0, "unit tests passed\n", "")

        return runner, calls

    def test_three_consecutive_runs_record_same_verified_commit(self) -> None:
        runner, calls = self._runner()
        report = run_release_gate(ROOT, runs=3, runner=runner)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["candidate"], "v1.0.0")
        self.assertEqual(report["verified_commit"], "a" * 40)
        self.assertEqual([item["status"] for item in report["runs"]], ["PASS", "PASS", "PASS"])
        self.assertEqual(len(calls), 11)

    def test_any_failed_check_fails_the_release_gate(self) -> None:
        runner, _ = self._runner(failed_command="unittest")
        report = run_release_gate(ROOT, runs=3, runner=runner)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual([item["status"] for item in report["runs"]], ["FAIL", "FAIL", "FAIL"])
        self.assertEqual(report["runs"][0]["checks"][1]["status"], "FAIL")

    def test_verified_commit_must_match_repository_head(self) -> None:
        runner, _ = self._runner()
        with self.assertRaises(ValueError):
            run_release_gate(ROOT, runs=1, runner=runner, verified_commit="b" * 40)

    def test_evidence_writer_rejects_repository_paths_and_writes_yaml_outside(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "release-gate.yaml"
            _write_evidence(target, {"status": "PASS", "verified_commit": "a" * 40})
            self.assertIn("status: PASS", target.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            _write_evidence(ROOT / "release-gate.yaml", {"status": "PASS"})


if __name__ == "__main__":
    unittest.main()
