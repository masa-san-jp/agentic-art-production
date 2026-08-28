from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from tools.lib.release import run_release_gate
from tools.lib.yaml_io import load_yaml
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
        self.assertEqual(len(calls), 38)

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

    def test_checkpoint_resumes_after_an_interrupted_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "release-gate.yaml"
            check_calls = 0

            def interrupted_runner(command, **kwargs):
                nonlocal check_calls
                if command[:2] == ["git", "rev-parse"]:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                if command[:2] == ["git", "status"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                check_calls += 1
                if check_calls == 2:
                    raise KeyboardInterrupt()
                if any(part.endswith("validate.py") for part in command):
                    output = "[]\n"
                elif any(part.endswith("run_evaluation.py") for part in command):
                    output = json.dumps({"status": "PASS"})
                else:
                    output = "unit tests passed\n"
                return subprocess.CompletedProcess(command, 0, output, "")

            with self.assertRaises(KeyboardInterrupt):
                run_release_gate(ROOT, runs=1, runner=interrupted_runner, checkpoint=checkpoint)
            saved = load_yaml(checkpoint)
            self.assertEqual(saved["status"], "IN_PROGRESS")
            self.assertEqual(saved["runs"][0]["checks"][0]["name"], "repository_validation")
            self.assertEqual(saved["next_check"]["name"], "unit_tests")

            runner, _ = self._runner()
            resumed = run_release_gate(ROOT, runs=1, runner=runner, checkpoint=checkpoint, resume=True)
            self.assertEqual(resumed["status"], "PASS")
            self.assertEqual(len(resumed["runs"][0]["checks"]), 4)

    def test_completed_checkpoint_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "release-gate.yaml"
            runner, _ = self._runner()
            first = run_release_gate(ROOT, runs=1, runner=runner, checkpoint=checkpoint)
            self.assertEqual(first["status"], "PASS")
            calls: list[list[str]] = []

            def no_check_runner(command, **kwargs):
                calls.append(command)
                if command[:2] == ["git", "rev-parse"]:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                if command[:2] == ["git", "status"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                raise AssertionError("a completed checkpoint must not rerun a gate check")

            resumed = run_release_gate(ROOT, runs=1, runner=no_check_runner, checkpoint=checkpoint, resume=True)
            self.assertEqual(resumed["status"], "PASS")
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
