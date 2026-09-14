from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_plan import _build_plan, main as build_plan_main
from tools.lib.planning import validate_plan_document
from tools.lib.yaml_io import load_yaml
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class InspirationAlignmentTest(unittest.TestCase):
    def _project(self, *, read_only: bool = False, path: str = "good") -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        output_root = Path(temporary.name) / "output"
        self.assertEqual(new_production_main([path, "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / f"production/{path}"
        hypothesis_path = project / "00_handoff/source-bundle/artifacts/production-hypotheses.yaml"
        hypotheses = load_yaml(hypothesis_path)
        hypotheses["hypotheses"][0].update({
            "proposition": "A repeated translucent structure makes one omission visible through viewer movement.",
            "intended_experience": ["The viewer notices the omitted interval while moving."],
            "includes": ["repeated translucent sheets", "one omitted interval", "side light"],
            "excludes": ["explanatory wall text"],
            "differentiation": {"statement": "Route and parallax produce recognition.", "precedent_refs": []},
            "feasibility": {"technical": "Render digitally; required skills: SVG composition.", "cost_band": "LOW", "duration_band": "HOURS", "venue_dependency": "UNKNOWN", "rights_status": "CLEAR", "safety_status": "CLEAR"},
        })
        hypothesis_path.write_text(__import__("yaml").safe_dump(hypotheses, sort_keys=False, allow_unicode=True), encoding="utf-8")
        prototype_path = project / "00_handoff/source-bundle/artifacts/prototype-plans.yaml"
        prototypes = load_yaml(prototype_path)
        prototype = prototypes["prototype_plans"][0]
        prototype.update({
            "method": "Render the accepted composition as a deterministic digital prototype.",
            "inputs": ["03_plan/production-plan.yaml dimensions", "03_plan/production-plan.yaml materials", "03_plan/production-plan.yaml quantity", "Selected hypothesis composition"],
            "constraints": ["Venue: local simulated preview only."],
            "executor_capability": "digital-prototype-renderer",
            "expected_evidence": "deterministic-svg-image-output",
        })
        prototype["tasks"] = [
            {"id": "PT001", "title": "Resolve structured production inputs", "depends_on": [], "completion_condition": "Inputs are resolved.", "effect_type": "READ_ONLY"},
            {"id": "PT002", "title": "Render deterministic digital prototype", "depends_on": ["PT001"], "completion_condition": "The SVG preview is written.", "effect_type": "READ_ONLY" if read_only else "REPOSITORY_WRITE"},
        ]
        prototype_path.write_text(__import__("yaml").safe_dump(prototypes, sort_keys=False, allow_unicode=True), encoding="utf-8")
        direction_path = project / "00_handoff/source-bundle/artifacts/creative-direction.md"
        direction_path.write_text("""# Direction\n\n## 作品の核\nA repeated translucent structure makes one omission visible through viewer movement.\n\n## 具体的な構成\n要素: repeated translucent sheets, one omitted interval, side light\n\n## 制作の完了経路\n1. Resolve structured production inputs.\n2. Render deterministic digital prototype.\n""", encoding="utf-8")
        self.assertIn(build_plan_main(["--project-root", str(project)]), (0, 1))
        return temporary, project

    def test_semantics_resources_and_completion_path_are_bound(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        plan_path = project / "03_plan/production-plan.yaml"
        plan = load_yaml(plan_path)
        alignment = plan["inspiration_alignment"]
        self.assertEqual("MATCH", alignment["status"])
        self.assertEqual("PH001", alignment["selected_hypothesis_id"])
        self.assertEqual(2, len(alignment["completion_path"]))
        self.assertTrue(alignment["checks"]["resource_bindings_complete"])
        self.assertEqual([], validate_plan_document(plan, repository=ROOT, plan_path=plan_path))

    def test_read_only_confirmation_cannot_qualify_alignment(self) -> None:
        temporary, project = self._project(read_only=True, path="read-only")
        self.addCleanup(temporary.cleanup)
        plan = _build_plan(project)
        alignment = plan["inspiration_alignment"]
        self.assertEqual("INCOMPLETE", alignment["status"])
        self.assertFalse(alignment["checks"]["production_action_present"])
        self.assertIn("inspiration_alignment", plan["readiness"]["unmet"])
        self.assertTrue(any(gap["rule"] == "PLANNING_INSPIRATION_ALIGNMENT" for gap in plan["gaps"]))

    def test_missing_completion_binding_stays_incomplete(self) -> None:
        temporary, project = self._project(path="missing-path")
        self.addCleanup(temporary.cleanup)
        direction_path = project / "00_handoff/source-bundle/artifacts/creative-direction.md"
        direction_path.write_text(direction_path.read_text(encoding="utf-8").replace("Render deterministic digital prototype", "Contact an unavailable external fabricator"), encoding="utf-8")
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 1)
        plan = _build_plan(project)
        self.assertEqual("INCOMPLETE", plan["inspiration_alignment"]["status"])
        self.assertFalse(plan["inspiration_alignment"]["checks"]["completion_path_complete"])


if __name__ == "__main__":
    unittest.main()
