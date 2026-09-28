from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tools import build_plan, build_prototype
from tools.new_production import main as new_production
from tools.lib.canonical import canonical_sha256, sha256_bytes
from tools.lib.prototype_render import PrototypeRenderError, render_digital_prototype
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.validate import validate_project

ROOT = Path(__file__).resolve().parents[1]


class PrototypeRendererAdapterTests(unittest.TestCase):
    @contextlib.contextmanager
    def _renderer_override(self, temporary: tempfile.TemporaryDirectory, *, name: str, script: Path, **settings: object):
        config_path = Path(temporary.name) / f"{name}.yaml"
        definition = {
            "kind": "command",
            "command": [sys.executable, str(script)],
            "version": "1.0.0",
            "generator": f"tests/{name}",
            "media_type": "image/png",
            "extension": "png",
            "timeout_seconds": 5,
            "max_stdout_bytes": 65536,
            "max_stderr_bytes": 16384,
            "environment_allowlist": [],
        }
        definition.update(settings)
        dump_yaml({"version": 1, "default": name, "renderers": {name: definition}}, config_path)
        previous = os.environ.get("AGENTIC_ART_PROTOTYPE_RENDERERS")
        os.environ["AGENTIC_ART_PROTOTYPE_RENDERERS"] = str(config_path)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("AGENTIC_ART_PROTOTYPE_RENDERERS", None)
            else:
                os.environ["AGENTIC_ART_PROTOTYPE_RENDERERS"] = previous

    @staticmethod
    def _script(directory: str, body: str) -> Path:
        path = Path(directory) / "renderer.py"
        path.write_text("import json, sys\n" + body + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _plan_and_prototype(project: Path) -> tuple[dict, dict]:
        return (
            load_yaml(project / "03_plan/production-plan.yaml"),
            load_yaml(project / "00_handoff/source-bundle/artifacts/prototype-plans.yaml")["prototype_plans"][0],
        )

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

        with self._renderer_override(temporary, name="fake-png", script=ROOT / "tests/fixtures/prototype_renderers/fake_png.py"):
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
        with self._renderer_override(temporary, name="fake-png", script=ROOT / "tests/fixtures/prototype_renderers/fake_png.py"):
            with self.assertRaisesRegex(PrototypeRenderError, "Git-external"):
                render_digital_prototype(project_root=project, plan=plan, prototype_plan=prototype, run_id="PRT001", renderer_name="fake-png")

    def test_legacy_svg_record_without_adapter_provenance_remains_valid(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, build_prototype.main(["--project-root", str(project)]))
        control_path = project / "04_prototype/prototype-control.yaml"
        control = load_yaml(control_path)
        output = control["runs"][0]["outputs"][0]
        for field in ("plan_id", "plan_revision", "plan_sha256", "renderer", "not_physical_evidence"):
            output.pop(field, None)
        control["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in control.items() if key != "integrity"})}
        dump_yaml(control, control_path)
        self.assertEqual(validate_project(project, ROOT), [])

    def test_renderer_output_directory_rejects_stale_artifacts(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        stale = project / "04_prototype/outputs/PRT001/stale.png"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale")
        plan, prototype = self._plan_and_prototype(project)
        with self._renderer_override(temporary, name="fake-png", script=ROOT / "tests/fixtures/prototype_renderers/fake_png.py"):
            with self.assertRaisesRegex(PrototypeRenderError, "empty before rendering"):
                render_digital_prototype(project_root=project, plan=plan, prototype_plan=prototype, run_id="PRT001", renderer_name="fake-png")
        self.assertTrue(stale.is_file())

    def test_command_renderer_timeout_nonzero_and_stdout_limit_clean_outputs(self) -> None:
        cases = (
            ("timeout", "import time; time.sleep(0.2)", {"timeout_seconds": 0.05}, "TIMEOUT"),
            ("nonzero", "raise SystemExit(7)", {}, "EXIT"),
            ("stdout", "print('x' * 1000)", {"max_stdout_bytes": 16}, "OUTPUT_LIMIT"),
            ("stderr", "print('x' * 1000, file=sys.stderr)", {"max_stderr_bytes": 16}, "OUTPUT_LIMIT"),
        )
        for name, body, settings, marker in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                temporary, project = self._project()
                self.addCleanup(temporary.cleanup)
                script = self._script(directory, body)
                plan, prototype = self._plan_and_prototype(project)
                with self._renderer_override(temporary, name=name, script=script, **settings):
                    with self.assertRaisesRegex(PrototypeRenderError, marker):
                        render_digital_prototype(project_root=project, plan=plan, prototype_plan=prototype, run_id="PRT001", renderer_name=name)
                self.assertFalse((project / "04_prototype/outputs/PRT001").exists())

    def test_command_renderer_rejects_dotdot_and_intermediate_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary, project = self._project()
            self.addCleanup(temporary.cleanup)
            plan, prototype = self._plan_and_prototype(project)
            dotdot = self._script(directory, "print(json.dumps({'output_relative_path': '04_prototype/outputs/PRT001/../escape.png', 'media_type': 'image/png', 'generator_id': 'tests/dotdot'}))")
            with self._renderer_override(temporary, name="dotdot", script=dotdot):
                with self.assertRaisesRegex(PrototypeRenderError, "safe and relative"):
                    render_digital_prototype(project_root=project, plan=plan, prototype_plan=prototype, run_id="PRT001", renderer_name="dotdot")
            other_run = self._script(directory, "print(json.dumps({'output_relative_path': '04_prototype/outputs/PRT999/prototype-1.png', 'media_type': 'image/png', 'generator_id': 'tests/other-run'}))")
            with self._renderer_override(temporary, name="other-run", script=other_run):
                with self.assertRaisesRegex(PrototypeRenderError, "requested run directory"):
                    render_digital_prototype(project_root=project, plan=plan, prototype_plan=prototype, run_id="PRT001", renderer_name="other-run")
            symlink = self._script(directory, "from pathlib import Path\nrun = Path('04_prototype/outputs/PRT001')\nexternal = Path(sys.argv[1])\nrun.rmdir()\nrun.symlink_to(external, target_is_directory=True)\nprint(json.dumps({'output_relative_path': '04_prototype/outputs/PRT001/prototype-1.png', 'media_type': 'image/png', 'generator_id': 'tests/symlink'}))")
            outside = Path(directory) / "outside"
            outside.mkdir()
            # The script receives no user-controlled path from the adapter
            # protocol; this argument only makes the symlink fixture portable.
            original = symlink.read_text(encoding="utf-8")
            symlink.write_text(original.replace("sys.argv[1]", repr(str(outside))), encoding="utf-8")
            with self._renderer_override(temporary, name="symlink", script=symlink):
                with self.assertRaisesRegex(PrototypeRenderError, "symlink|escaped"):
                    render_digital_prototype(project_root=project, plan=plan, prototype_plan=prototype, run_id="PRT001", renderer_name="symlink")

    def test_help_does_not_load_a_broken_renderer_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.yaml"
            path.write_text("version: [broken\n", encoding="utf-8")
            previous = os.environ.get("AGENTIC_ART_PROTOTYPE_RENDERERS")
            os.environ["AGENTIC_ART_PROTOTYPE_RENDERERS"] = str(path)
            try:
                with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()):
                    build_prototype.build_parser().parse_args(["--help"])
                self.assertEqual(raised.exception.code, 0)
            finally:
                if previous is None:
                    os.environ.pop("AGENTIC_ART_PROTOTYPE_RENDERERS", None)
                else:
                    os.environ["AGENTIC_ART_PROTOTYPE_RENDERERS"] = previous


if __name__ == "__main__":
    unittest.main()
