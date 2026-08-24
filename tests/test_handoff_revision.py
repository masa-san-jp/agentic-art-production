from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.canonical import canonical_sha256, handoff_sha256, sha256_bytes
from tools.lib.execution import ExecutionManager
from tools.lib.yaml_io import dump_yaml, load_jsonl, load_yaml
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class HandoffRevisionTests(unittest.TestCase):
    def _revised_bundle(
        self,
        directory: Path,
        *,
        handoff_id: str = "HO002",
        revision: int = 2,
        supersedes: str | None = "HO001",
        statement: str = "Keep the revised element spacing observable.",
    ) -> Path:
        bundle = directory / f"handoff-{handoff_id}"
        shutil.copytree(FIXTURE, bundle)

        requirements_path = bundle / "artifacts/production-requirements.yaml"
        requirements = load_yaml(requirements_path)
        requirements["requirements"][0]["statement"] = statement
        dump_yaml(requirements, requirements_path)

        handoff_path = bundle / "production-handoff.yaml"
        handoff = load_yaml(handoff_path)
        handoff["handoff_id"] = handoff_id
        handoff["revision"] = revision
        if supersedes is None:
            handoff.pop("supersedes", None)
        else:
            handoff["supersedes"] = supersedes
        handoff["requirements"][0]["statement"] = statement
        handoff["integrity"] = {"content_sha256": handoff_sha256(handoff)}
        dump_yaml(handoff, handoff_path)

        manifest_path = bundle / "manifest.yaml"
        manifest = load_yaml(manifest_path)
        manifest["bundle_id"] = f"HB-{handoff_id}-R{revision}"
        manifest["handoff_key"] = {"handoff_id": handoff_id, "revision": revision}
        entries = []
        for entry in manifest["files"]:
            path = bundle / entry["path"]
            entry["size_bytes"] = path.stat().st_size
            entry["sha256"] = sha256_bytes(path.read_bytes())
            entries.append({"path": entry["path"], "size_bytes": entry["size_bytes"], "sha256": entry["sha256"]})
        manifest["integrity"]["file_set_sha256"] = canonical_sha256(sorted(entries, key=lambda item: item["path"]))
        dump_yaml(manifest, manifest_path)
        return bundle

    def _project_with_recorded_output(self, output_root: Path) -> Path:
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        project = output_root / "production/smoke"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        manager = ExecutionManager(project, ROOT)
        manager.init()
        manager.record_output(
            {
                "schema_version": "1.0.0",
                "output_id": "OUT001",
                "revision": 1,
                "project_id": "production/smoke",
                "deliverable_id": "DL001",
                "version": "1.0.1",
                "asset_ref": {
                    "asset_id": "AS001",
                    "uri": "urn:asset:synthetic:OUT001",
                    "version": "1.0.1",
                    "sha256": "sha256:" + "1" * 64,
                    "media_type": "image/png",
                    "rights_status": "PROJECT_INTERNAL",
                },
                "status": "CANDIDATE",
                "source_task_ids": ["TK001"],
                "quality_result_ids": [],
                "created_at": "2026-08-12T12:00:00+09:00",
                "created_by": {"kind": "AGENT", "id": "test-agent"},
                "supersedes": None,
            },
            occurred_at="2026-08-12T12:00:00+09:00",
            actor_kind="AGENT",
            actor_id="test-agent",
            idempotency_key="output/OUT001/1",
        )
        return project

    def test_superseding_handoff_is_accepted_into_the_existing_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            project = self._project_with_recorded_output(output_root)
            revised = self._revised_bundle(root)

            self.assertEqual(new_production_main(["smoke", "--handoff", str(revised), "--output-root", str(output_root)]), 0)

            manifest = load_yaml(project / "manifest.yaml")
            self.assertEqual(manifest["handoff"]["handoff_id"], "HO002")
            self.assertEqual(manifest["handoff"]["revision"], 2)

            receipt = load_yaml(project / "00_handoff/handoff-receipt.yaml")
            self.assertEqual(receipt["handoff_id"], "HO002")
            self.assertEqual(receipt["receipt_id"], "RC002")

            ledger = load_jsonl(project / "00_handoff/handoff-receipts.jsonl")
            self.assertEqual([entry["handoff_id"] for entry in ledger], ["HO001", "HO002"])

            handoff = load_yaml(project / "00_handoff/production-handoff.yaml")
            self.assertEqual(handoff["handoff_id"], "HO002")

            # What the project made must survive a revision of what it was asked to make.
            outputs = load_yaml(project / "05_execution/output-versions.yaml")
            self.assertEqual([record["output_id"] for record in outputs["records"]], ["OUT001"])

            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            rendered = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
            self.assertIn("Keep the revised element spacing observable.", rendered)
            self.assertNotIn("Keep the repeated element spacing observable.", rendered)

    def test_handoff_that_supersedes_something_else_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            project = self._project_with_recorded_output(output_root)
            unrelated = self._revised_bundle(root, handoff_id="HO003", revision=3, supersedes="HO999")

            self.assertEqual(new_production_main(["smoke", "--handoff", str(unrelated), "--output-root", str(output_root)]), 1)

            manifest = load_yaml(project / "manifest.yaml")
            self.assertEqual(manifest["handoff"]["handoff_id"], "HO001")
            self.assertFalse((project / "00_handoff/handoff-receipts.jsonl").is_file() and
                             len(load_jsonl(project / "00_handoff/handoff-receipts.jsonl")) > 1)

    def test_handoff_without_supersedes_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            project = self._project_with_recorded_output(output_root)
            orphan = self._revised_bundle(root, handoff_id="HO004", revision=4, supersedes=None)

            self.assertEqual(new_production_main(["smoke", "--handoff", str(orphan), "--output-root", str(output_root)]), 1)
            manifest = load_yaml(project / "manifest.yaml")
            self.assertEqual(manifest["handoff"]["handoff_id"], "HO001")

    def test_reaccepting_the_same_handoff_stays_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            project = self._project_with_recorded_output(output_root)
            before = (project / "manifest.yaml").read_bytes()

            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)

            self.assertEqual((project / "manifest.yaml").read_bytes(), before)
            ledger = load_jsonl(project / "00_handoff/handoff-receipts.jsonl")
            self.assertEqual([entry["handoff_id"] for entry in ledger], ["HO001"])

    def test_second_revision_chains_from_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            project = self._project_with_recorded_output(output_root)
            first = self._revised_bundle(root)
            self.assertEqual(new_production_main(["smoke", "--handoff", str(first), "--output-root", str(output_root)]), 0)
            second = self._revised_bundle(root, handoff_id="HO003", revision=3, supersedes="HO002", statement="Keep the third element spacing observable.")

            self.assertEqual(new_production_main(["smoke", "--handoff", str(second), "--output-root", str(output_root)]), 0)

            ledger = load_jsonl(project / "00_handoff/handoff-receipts.jsonl")
            self.assertEqual([entry["handoff_id"] for entry in ledger], ["HO001", "HO002", "HO003"])
            self.assertEqual(load_yaml(project / "00_handoff/handoff-receipt.yaml")["receipt_id"], "RC003")


if __name__ == "__main__":
    unittest.main()
