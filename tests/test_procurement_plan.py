from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_plan import _procurement_candidates, main as build_plan_main
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

    def test_direct_materials_cover_routes_and_supplier_provenance(self) -> None:
        materials = [
            {
                "id": "MT001", "name": "stock panel", "specification": "standard stock panel",
                "quantity": {"value": "1", "unit": "item"}, "rights_status": "CLEAR", "safety_status": "CLEAR",
                "supplier_name": "Example Stock", "source_url": "https://example.test/stock",
                "trace_refs": ["SRC001"],
            },
            {
                "id": "MT002", "name": "cut panel", "specification": "cut panel",
                "quantity": {"value": "1", "unit": "item"}, "rights_status": "CLEAR", "safety_status": "CLEAR",
                "trace_refs": ["SRC002"],
            },
            {
                "id": "MT003", "name": "reclaimed panel", "specification": "reclaimed panel",
                "quantity": {"value": "1", "unit": "item"}, "rights_status": "CLEAR", "safety_status": "CLEAR",
                "trace_refs": ["SRC003"],
            },
            {
                "id": "MT004", "name": "custom panel", "specification": "custom made-to-order panel",
                "quantity": {"value": "1", "unit": "item"}, "rights_status": "CLEAR", "safety_status": "CLEAR",
                "trace_refs": ["SRC004"],
            },
            {
                "id": "MT005", "name": "bad source", "specification": "standard panel",
                "quantity": {"value": "1", "unit": "item"}, "rights_status": "CLEAR", "safety_status": "CLEAR",
                "supplier_name": "Unverified Supplier", "source_url": "https://example.test/stock?token=private",
                "trace_refs": ["SRC005"],
            },
            {
                "id": "MT006", "name": "negative standard", "specification": "non-standard panel",
                "quantity": {"value": "1", "unit": "item"}, "rights_status": "CLEAR", "safety_status": "CLEAR",
                "trace_refs": ["SRC006"],
            },
            {
                "id": "MT007", "name": "execute panel", "specification": "execute panel",
                "quantity": {"value": "1", "unit": "item"}, "rights_status": "CLEAR", "safety_status": "CLEAR",
                "trace_refs": ["SRC007"],
            },
        ]

        candidates = _procurement_candidates(materials)
        routes = {candidate["material_id"]: candidate["route_type"] for candidate in candidates}
        self.assertEqual(routes["MT001"], "STANDARD_STOCK")
        self.assertEqual(routes["MT002"], "FABRICATION")
        self.assertEqual(routes["MT003"], "BORROW_OR_REUSE")
        self.assertEqual(routes["MT004"], "CUSTOM_ORDER")
        self.assertEqual(routes["MT006"], "UNKNOWN")
        self.assertEqual(routes["MT007"], "UNKNOWN")

        named = next(candidate for candidate in candidates if candidate["material_id"] == "MT001")
        self.assertEqual(named["supplier_name"], "Example Stock")
        self.assertEqual(named["source_url"], "https://example.test/stock")
        invalid = next(candidate for candidate in candidates if candidate["material_id"] == "MT005")
        self.assertIsNone(invalid["supplier_name"])
        self.assertIsNone(invalid["source_url"])
        self.assertTrue(all(
            not candidate["supplier_name"] or candidate["source_url"]
            for candidate in candidates
        ))


if __name__ == "__main__":
    unittest.main()
