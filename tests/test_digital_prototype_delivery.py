from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import build_plan, plan_actionability
from tools.build_prototype import main as build_prototype
from tools.new_production import main as new_production
from tools.public_plan_attestation import automatic_plan_review, build_attestation, verify_attestation
from tools.lib.canonical import canonical_sha256
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.lib.evidence import EvidenceManager
from tests.test_bootstrap import BootstrapContractTests


ROOT = Path(__file__).resolve().parents[1]


class DigitalPrototypeDeliveryTests(unittest.TestCase):
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
        BootstrapContractTests()._refresh_bundle_manifest(bundle)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, new_production(["digital", "--handoff", str(bundle), "--output-root", str(output)]))
        project = output / "production/digital"
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

    def test_digital_delivery_is_traceable_deterministic_and_actionable(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(0, build_prototype(["--project-root", str(project)]))
        control_path = project / "04_prototype/prototype-control.yaml"
        control = load_yaml(control_path)
        run = control["runs"][0]
        self.assertEqual("COMPLETE", run["status"])
        self.assertEqual("NOT_REQUIRED", run["external_validation_status"])
        self.assertTrue(run["evidence_refs"])
        output = run["outputs"][0]
        source = project / output["relative_path"]
        preview = project / "03_plan/media/prototype/PRT001-1.svg"
        self.assertTrue(source.is_file())
        self.assertTrue(preview.is_file())
        self.assertEqual(source.read_bytes(), preview.read_bytes())
        self.assertIn("試作（デジタル、simulated）", (project / "03_plan/production-plan.md").read_text(encoding="utf-8"))
        self.assertIn("media/prototype/PRT001-1.svg", (project / "03_plan/production-plan.md").read_text(encoding="utf-8"))
        self.assertIn("120 cm", source.read_text(encoding="utf-8"))
        self.assertIn("synthetic translucent sheet", source.read_text(encoding="utf-8"))
        evidence = EvidenceManager(project, ROOT).records()[0]
        self.assertEqual(output["sha256"], evidence["content_sha256"])
        self.assertEqual("PROTOTYPE", evidence["evidence_type"])
        self.assertEqual("READY", plan_actionability.verify(project)["prototype_status"])

        first = {path: path.read_bytes() for path in (control_path, source, preview, project / "05_execution/evidence-log.jsonl", project / "05_execution/evidence-register.yaml")}
        self.assertEqual(0, build_prototype(["--project-root", str(project)]))
        self.assertEqual(first, {path: path.read_bytes() for path in first})

    def test_preview_tamper_is_visible_to_actionability_and_attestation(self) -> None:
        temporary, project = self._project()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(0, build_prototype(["--project-root", str(project)]))
        code = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        attestation = build_attestation(project, automatic_plan_review(project), producer_commit=code, generated_at="2026-09-14T00:00:00Z")
        verify_attestation(project, attestation)
        preview = project / "03_plan/media/prototype/PRT001-1.svg"
        preview.write_bytes(preview.read_bytes().replace(b"fill=\"#ffffff\"", b"fill=\"#000000\"", 1))
        result = plan_actionability.verify(project)
        self.assertEqual("MISSING", result["prototype_status"])
        self.assertTrue(any("PROTOTYPE_OUTPUT_MISSING" in finding for finding in result["findings"]))
        with self.assertRaisesRegex(ValueError, "PUBLIC_ASSET_HASH_MISMATCH"):
            verify_attestation(project, attestation)


if __name__ == "__main__":
    unittest.main()
