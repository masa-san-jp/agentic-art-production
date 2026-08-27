from __future__ import annotations

import unittest

from tools.lib.evaluation import run_evaluation


class EvaluationMatrixTests(unittest.TestCase):
    def test_representative_matrix_passes(self) -> None:
        report = run_evaluation()
        self.assertEqual("PASS", report["status"])
        self.assertEqual({"COMPLETE", "COMPLETE_WITH_GAPS", "BLOCKED"}, {scenario["state"] for scenario in report["scenarios"]})
        self.assertEqual(
            {"OFFLINE_E2E", "CONTRACT_TRACEABILITY", "DETERMINISM_IDEMPOTENCY", "RESUME_EFFECT_IDEMPOTENCY", "APPROVAL_GATES", "SECURITY_CHAOS_RECOVERY", "NON_ISOMORPHIC_F1_F6"},
            {check["id"] for check in report["checks"]},
        )
        self.assertTrue(all(check["status"] == "PASS" for check in report["checks"]))
        variants = next(check["evidence"] for check in report["checks"] if check["id"] == "NON_ISOMORPHIC_F1_F6")
        self.assertEqual({"F1", "F2", "F3", "F4", "F5", "F6"}, {variant["id"] for variant in variants})

    def test_representative_matrix_is_deterministic(self) -> None:
        first = run_evaluation()
        second = run_evaluation()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
