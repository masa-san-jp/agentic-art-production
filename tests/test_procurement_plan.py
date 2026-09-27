from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.schema import load_schema, validate_instance
from tools.lib.yaml_io import load_yaml
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/task-matrix"


class ProcurementPlanTests(unittest.TestCase):
    def test_candidates_cover_materials_without_authorizing_procurement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["matrix", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/matrix"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)

            plan = load_yaml(project / "03_plan/production-plan.yaml")
            procurement = load_yaml(project / "03_plan/procurement-plan.yaml")
            self.assertEqual(procurement["status"], "NOT_AUTHORIZED")
            self.assertEqual(
                [candidate["material_id"] for candidate in procurement["candidates"]],
                [material["id"] for material in plan["materials"]],
            )
            self.assertEqual(procurement["candidates"][0]["route_type"], "UNKNOWN")
            self.assertTrue(procurement["candidates"][0]["basis"])
            self.assertIsNone(procurement["candidates"][0]["supplier_name"])
            self.assertIsNone(procurement["candidates"][0]["source_url"])
            self.assertTrue(procurement["candidates"][0]["checks"])

            schema_path = ROOT / "schemas/procurement-plan.schema.json"
            self.assertEqual(
                validate_instance(
                    procurement,
                    load_schema(schema_path),
                    schema_path=schema_path,
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
