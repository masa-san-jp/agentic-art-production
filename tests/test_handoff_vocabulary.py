from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.lib.diagnostics import DiagnosticError
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.new_production import _assert_planning_vocabulary


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class HandoffVocabularyTests(unittest.TestCase):
    """Accepting a bundle claims production can work from it, so a value the planner refuses is rejected here."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.bundle = Path(self.temporary.name) / "bundle"
        shutil.copytree(FIXTURE, self.bundle)
        self.addCleanup(self.temporary.cleanup)

    def _set_result(self, value: str) -> None:
        path = self.bundle / "artifacts/acceptance-tests.yaml"
        document = load_yaml(path)
        document["acceptance_tests"][0]["result"] = value
        dump_yaml(document, path)

    def test_a_result_the_planner_accepts_passes_acceptance(self):
        self._set_result("EXTERNAL_VALIDATION_REQUIRED")

        _assert_planning_vocabulary(self.bundle, ROOT)

    def test_a_result_outside_the_planning_vocabulary_is_rejected_at_acceptance(self):
        self._set_result("PENDING")

        with self.assertRaises(DiagnosticError):
            _assert_planning_vocabulary(self.bundle, ROOT)

    def test_the_rejection_names_the_test_and_the_accepted_values(self):
        self._set_result("PENDING")

        try:
            _assert_planning_vocabulary(self.bundle, ROOT)
        except DiagnosticError as exc:
            self.assertIn("AT001", exc.finding.reason)
            self.assertIn("NOT_RUN", exc.finding.remediation)

    def test_the_shipped_fixture_passes_unchanged(self):
        _assert_planning_vocabulary(self.bundle, ROOT)


if __name__ == "__main__":
    unittest.main()
