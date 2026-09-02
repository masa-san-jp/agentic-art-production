from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.new_production import main as new_production_main
from tools.validate import validate_project


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class VisualPackageTests(unittest.TestCase):
    def _build(self, directory: str) -> Path:
        output_root = Path(directory) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / "production/smoke"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        self.assertEqual(validate_project(project, ROOT), [])
        return project

    def test_board_and_conceptual_mockup_are_viewable_and_linked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._build(directory)
            package = load_yaml(project / "03_plan/visual-package.yaml")
            self.assertEqual(package["status"], "READY")
            self.assertEqual(package["board"]["kind"], "BOARD")
            self.assertEqual(package["mockup"]["kind"], "MOCKUP")
            self.assertEqual(package["mockup"]["representation"], "CONCEPTUAL")
            self.assertTrue((project / package["board"]["relative_path"]).read_bytes().startswith(b"<svg"))
            self.assertTrue((project / package["mockup"]["relative_path"]).read_bytes().startswith(b"<svg"))
            rendered = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
            self.assertIn("visual-reference-board.svg](visual-package/visual-reference-board.svg)", rendered)
            self.assertIn("concept-mockup.svg](visual-package/concept-mockup.svg)", rendered)
            self.assertIn("PHYSICAL_EXTERNAL validation: NOT_RUN", rendered)
            self.assertIn("source_repository_at_commit", str(package["board"]["provenance"]))

    def test_repeated_builds_have_identical_fixture_assets_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_project = self._build(first)
            second_project = self._build(second)
            for relative in (
                "03_plan/visual-package.yaml",
                "03_plan/visual-package/visual-reference-board.svg",
                "03_plan/visual-package/concept-mockup.svg",
            ):
                self.assertEqual((first_project / relative).read_bytes(), (second_project / relative).read_bytes(), relative)

    def test_missing_link_and_tampered_hash_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._build(directory)
            package = load_yaml(project / "03_plan/visual-package.yaml")
            board_path = project / package["board"]["relative_path"]
            board_path.unlink()
            rules = {finding.rule for finding in validate_project(project, ROOT)}
            self.assertIn("VISUAL_PACKAGE_FILE_MISSING", rules)

            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            board_path.write_bytes(board_path.read_bytes() + b"\n<!-- tampered -->\n")
            rules = {finding.rule for finding in validate_project(project, ROOT)}
            self.assertIn("VISUAL_PACKAGE_HASH", rules)

    def test_unresolved_rights_on_adopted_source_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._build(directory)
            package_path = project / "03_plan/visual-package.yaml"
            package = load_yaml(package_path)
            package["source_refs"][0]["adoption_status"] = "ADOPTED"
            package["source_refs"][0]["rights_status"] = "UNKNOWN"
            dump_yaml(package, package_path)
            rules = {finding.rule for finding in validate_project(project, ROOT)}
            self.assertIn("VISUAL_PACKAGE_RIGHTS", rules)

    def test_schema_rejects_unknown_visual_fields_and_invalid_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._build(directory)
            package_path = project / "03_plan/visual-package.yaml"
            package = load_yaml(package_path)
            package["board"]["unexpected"] = "must be rejected"
            dump_yaml(package, package_path)
            rules = {finding.rule for finding in validate_project(project, ROOT)}
            self.assertIn("SCHEMA_VALIDATION", rules)

            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            package = load_yaml(package_path)
            package["mockup"]["kind"] = "BOARD"
            dump_yaml(package, package_path)
            rules = {finding.rule for finding in validate_project(project, ROOT)}
            self.assertIn("SCHEMA_VALIDATION", rules)


if __name__ == "__main__":
    unittest.main()
