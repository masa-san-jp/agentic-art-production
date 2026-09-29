from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_plan import _calendar_task_schedule, main as build_plan_main
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class DerivedPlanFieldsTests(unittest.TestCase):
    def _new_project(self, directory: str) -> Path:
        output_root = Path(directory) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        return output_root / "production/smoke"

    def test_budget_controls_are_derived_and_empty_asset_register_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._new_project(directory)
            stale_asset_register = project / "02_specification/asset-register.yaml"
            stale_asset_register.write_text("assets: []\n", encoding="utf-8")
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            budget = load_yaml(project / "03_plan/budget.yaml")
            self.assertEqual(budget["contingency"], {"amount": "1000", "currency": "JPY"})
            self.assertEqual(budget["approval_threshold"], {"amount": "11000", "currency": "JPY"})
            self.assertFalse((project / "02_specification/asset-register.yaml").exists())

    def test_nonempty_asset_register_is_not_silently_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._new_project(directory)
            stale_asset_register = project / "02_specification/asset-register.yaml"
            dump_yaml({"assets": [{"id": "AS001"}]}, stale_asset_register)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 1)
            self.assertTrue(stale_asset_register.exists())

    def test_explicit_calendar_input_derives_calendar_schedule_and_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._new_project(directory)
            dump_yaml({
                "schema_version": "1.0.0",
                "start_at": "2026-10-01T09:00:00+09:00",
                "due_at": "2026-10-01T11:00:00+09:00",
                "baseline_status": "BASELINED",
            }, project / "01_scope/calendar-input.yaml")
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            self.assertEqual(plan["schedule"]["mode"], "CALENDAR")
            self.assertEqual(plan["schedule"]["baseline_status"], "BASELINED")
            self.assertTrue(all(item["start_at"] and item["due_at"] for item in plan["schedule"]["task_schedule"]))
            self.assertEqual(plan["schedule"]["task_schedule"][0]["start_at"], "2026-10-01T09:00:00+09:00")
            self.assertEqual(plan["schedule"]["task_schedule"][1]["due_at"], "2026-10-01T11:00:00+09:00")
            self.assertTrue(any("operating hours or availability" in gap for gap in plan["schedule"]["gaps"]))

    def test_calendar_schedule_uses_dependency_earliest_start_and_unknown_duration_stays_relative(self) -> None:
        tasks = [
            {"id": "TK003", "depends_on": ["TK001"]},
            {"id": "TK002", "depends_on": []},
            {"id": "TK001", "depends_on": []},
        ]
        durations = {
            "TK001": {"value": "60", "unit": "min"},
            "TK002": {"value": "30", "unit": "min"},
            "TK003": {"value": "15", "unit": "min"},
        }
        entries, mode, baseline, gaps = _calendar_task_schedule(
            tasks,
            durations,
            {"start_at": "2026-10-01T09:00:00+09:00", "due_at": "2026-10-01T10:15:00+09:00", "baseline_status": "BASELINED"},
            [],
        )
        by_id = {entry["task_id"]: entry for entry in entries}
        self.assertEqual(mode, "CALENDAR")
        self.assertEqual(baseline, "BASELINED")
        self.assertEqual(by_id["TK002"]["start_at"], "2026-10-01T09:00:00+09:00")
        self.assertEqual(by_id["TK003"]["start_at"], "2026-10-01T10:00:00+09:00")
        self.assertTrue(any("operating hours or availability" in gap for gap in gaps))
        _, unknown_mode, unknown_baseline, unknown_gaps = _calendar_task_schedule(
            tasks, durations, {"start_at": "2026-10-01T09:00:00+09:00", "due_at": None, "baseline_status": "BASELINED"}, ["unknown duration"]
        )
        self.assertEqual(unknown_mode, "RELATIVE")
        self.assertEqual(unknown_baseline, "PROVISIONAL")
        self.assertTrue(any("unknown duration" in gap for gap in unknown_gaps))

    def test_without_calendar_input_schedule_remains_relative_with_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._new_project(directory)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            self.assertEqual(plan["schedule"]["mode"], "RELATIVE")
            self.assertTrue(any("schedule remains relative" in gap["statement"] for gap in plan["gaps"]))


if __name__ == "__main__":
    unittest.main()
