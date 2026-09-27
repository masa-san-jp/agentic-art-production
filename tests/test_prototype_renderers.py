from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from tools import build_plan, build_prototype
from tools.new_production import main as new_production
from tools.lib.canonical import canonical_sha256, sha256_bytes
from tools.lib.prototype_render import PrototypeRenderError, render_digital_prototype
from tools.lib.yaml_io import dump_yaml, load_yaml

ROOT = Path(__file__).resolve().parents[1]


class PrototypeRendererAdapterTests(unittest.TestCase):
    def _project(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        output = Path(temporary.name)
        bundle = output / "bundle"
        shutil.copytree(ROOT / "tests/fixtures/handoff/minimal", bundle)
        prototype_path = bundle / "artifacts/prototype-plans.yaml"
        prototype = load_yaml(prototype_path)
        source = prototype["prototype_plans"][0]
        source["executor_capability"] = "digital-prototype-renderer"
        for task in source["tasks"]:
            task["effect_type"] = "READ_ONLY"
        source["materials"] = [{
            "id": "MAT001",
            "name": "synthetic translucent sheet",
            "specification": "fixture-only sheet for a simulated preview",
            "quantity": {"value": "1", "unit": "sheet"},
            "rights_status": "PROJECT_INTERNAL",
            "safety_status": "CLEAR",
            "status": "APPROVED",
        }]
        dump_yaml(prototype, prototype_path)
        # Reuse the fixture manifest refresher without importing the test
        # module, which would make unittest discover the suite twice.
        manifest = load_yaml(bundle / "manifest.yaml")
        for entry in manifest["files"]:
            raw = (bundle / entry["path"]).read_bytes()
            entry["size_bytes"] = len(raw)
            entry["sha256"] = sha256_bytes(raw)
        manifest["integrity"]["file_set_sha256"] = canonical_sha256(sorted([
            {key: entry[key] for key in ("path", "size_bytes", "sha256")}
            for entry in manifest["files"]
        ], key=lambda item: item["path"]))
        dump_yaml(manifest, bundle / "manifest.yaml")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, new_production(["digital", "--handoff", str(bundle), "--output-root", str(output)]))
        project = output / "production/digital"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, build_plan.main(["--project-root", str(project)]))
        plan_path = project / "03_plan/production-plan.yaml"
        plan = load_yaml(plan_path)
        plan["technical_specifications"][0]["target"] = {
            "kind": "QUANTITY",
            "statement": "Overall height of the simulated object.",
            "quantity": {"value": "120", "unit": "cm"},
        }
        plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})}
        dump_yaml(plan, plan_path)
        (project / "03_plan/production-plan.md").write_text(build_plan._render_human_plan(project, plan), encoding="utf-8")
        return temporary, project

    def test_declared_fake_png_renderer_changes_media_type_and_keeps_provenance(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, build_prototype.main(["--project-root", str(project), "--renderer", "fake-png"]))
        control = load_yaml(project / "04_prototype/prototype-control.yaml")
        output = control["runs"][0]["outputs"][0]
        self.assertEqual("image/png", output["media_type"])
        self.assertEqual("fake-png", output["renderer"]["name"])
        self.assertEqual("1.0.0", output["renderer"]["version"])
        self.assertEqual("tests/fake-png-renderer", output["renderer"]["generator_id"])
        self.assertTrue(output["not_physical_evidence"])
        self.assertEqual("PL001", output["plan_id"])
        self.assertEqual(1, output["plan_revision"])
        self.assertTrue((project / output["relative_path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue((project / "03_plan/media/prototype/PRT001-1.png").is_file())
        body = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
        self.assertIn("media/prototype/PRT001-1.png", body)

    def test_renderer_keeps_git_external_boundary(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        (project / ".git").mkdir()
        plan = load_yaml(project / "03_plan/production-plan.yaml")
        prototype = load_yaml(project / "00_handoff/source-bundle/artifacts/prototype-plans.yaml")["prototype_plans"][0]
        with self.assertRaisesRegex(PrototypeRenderError, "Git-external"):
            render_digital_prototype(project_root=project, plan=plan, prototype_plan=prototype, run_id="PRT001")


if __name__ == "__main__":
    unittest.main()
