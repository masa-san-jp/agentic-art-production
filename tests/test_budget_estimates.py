from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class BudgetEstimateTests(unittest.TestCase):
    def test_low_cost_band_produces_low_confidence_planning_amount(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            budget = load_yaml(project / "03_plan/budget.yaml")
            self.assertEqual(budget["baseline_total"], {"amount": "10000", "currency": "JPY"})
            self.assertEqual(budget["items"][0]["amount"], {"amount": "10000", "currency": "JPY"})
            self.assertEqual(budget["items"][0]["confidence"], "LOW")
            self.assertIn("cost-estimation.yaml v1", budget["items"][0]["basis"])
            self.assertIn("not observed market prices", budget["items"][0]["basis"])
            self.assertIsNone(budget["contingency"])
            self.assertIsNone(budget["approval_threshold"])

    def test_unknown_cost_band_remains_unestimated_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            prototype_path = project / "00_handoff/source-bundle/artifacts/prototype-plans.yaml"
            prototype = load_yaml(prototype_path)
            prototype["prototype_plans"][0]["estimated_cost_band"] = "UNKNOWN"
            dump_yaml(prototype, prototype_path)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            budget = load_yaml(project / "03_plan/budget.yaml")
            self.assertIsNone(budget["baseline_total"])
            self.assertIsNone(budget["items"][0]["amount"])
            self.assertIn("UNKNOWN", budget["items"][0]["basis"])
            self.assertIn("confirmed cost band", budget["items"][0]["basis"])
            self.assertTrue(any("every budget item has UNKNOWN" in gap for gap in budget["gaps"]))


if __name__ == "__main__":
    unittest.main()
