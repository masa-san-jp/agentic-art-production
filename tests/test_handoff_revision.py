from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_plan import main as build_plan_main
from tools.build_prototype import main as build_prototype_main
from tools.lib.canonical import canonical_sha256, sha256_bytes
from tools.lib.runtime import Runtime
from tools.lib.yaml_io import dump_yaml, load_jsonl, load_yaml
from tools.new_production import main as new_production_main
from tools.validate import validate_project


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/task-matrix"


def _revision_bundle(destination: Path, revision: int = 2, supersedes: str = "HO002") -> Path:
    shutil.copytree(FIXTURE, destination)
    handoff_path = destination / "production-handoff.yaml"
    handoff = load_yaml(handoff_path)
    handoff["revision"] = revision
    handoff["supersedes"] = supersedes
    handoff["requirements"][0]["statement"] = "Keep the revised explicit task trace observable."
    handoff["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in handoff.items() if key != "integrity"})}
    dump_yaml(handoff, handoff_path)
    requirements_path = destination / "artifacts/production-requirements.yaml"
    requirements = load_yaml(requirements_path)
    requirements["requirements"][0]["statement"] = handoff["requirements"][0]["statement"]
    dump_yaml(requirements, requirements_path)
    manifest_path = destination / "manifest.yaml"
    manifest = load_yaml(manifest_path)
    entries = []
    for path in sorted(item for item in destination.rglob("*") if item.is_file() and item.name != "manifest.yaml"):
        relative = path.relative_to(destination).as_posix()
        entries.append({"path": relative, "role": "HANDOFF" if relative == "production-handoff.yaml" else "ARTIFACT", "media_type": "application/yaml" if path.suffix in {".yaml", ".json"} else "text/markdown", "size_bytes": path.stat().st_size, "sha256": sha256_bytes(path.read_bytes())})
    manifest["handoff_key"] = {"handoff_id": handoff["handoff_id"], "revision": revision}
    manifest["files"] = entries
    manifest["integrity"] = {"file_set_sha256": canonical_sha256([{key: entry[key] for key in ("path", "size_bytes", "sha256")} for entry in entries])}
    dump_yaml(manifest, manifest_path)
    return destination


class HandoffRevisionTests(unittest.TestCase):
    def _prepared_project(self, root: Path) -> Path:
        output = root / "output"
        self.assertEqual(new_production_main(["revision-test", "--handoff", str(FIXTURE), "--output-root", str(output)]), 0)
        project = output / "production/revision-test"
        self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
        self.assertEqual(build_prototype_main(["--project-root", str(project)]), 0)
        runtime = Runtime(project, ROOT)
        runtime.bootstrap(occurred_at="2026-08-25T12:00:00+09:00", actor_kind="SYSTEM", actor_id="test/runtime")
        runtime.initialize_task_graph(occurred_at="2026-08-25T12:00:01+09:00", actor_kind="SYSTEM", actor_id="test/runtime")
        self.assertEqual(runtime.replay()["state"], "PLANNING")
        return project

    def test_revision_preserves_history_resets_task_graph_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._prepared_project(root)
            candidate = _revision_bundle(root / "candidate")
            args = [
                "revision-test", "--handoff", str(candidate), "--output-root", str(root / "output"),
                "--accept-revision", "--occurred-at", "2026-08-25T12:01:00+09:00",
                "--actor-kind", "HUMAN", "--actor-id", "human/test", "--idempotency-key", "handoff/HO002/r2",
            ]
            self.assertEqual(new_production_main(args), 0)
            handoff = load_yaml(project / "00_handoff/production-handoff.yaml")
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            state = Runtime(project, ROOT).replay()
            self.assertEqual(handoff["revision"], 2)
            self.assertEqual(plan["plan_revision"], 2)
            self.assertEqual(state["state"], "PLANNING")
            self.assertNotIn("task_graph_sha256", state)
            self.assertTrue((project / "00_handoff/handoff-log.jsonl").is_file())
            self.assertTrue((project / "00_handoff/handoff-receipts.yaml").is_file())
            self.assertTrue((project / "00_handoff/source-bundles/HO002-r1/manifest.yaml").is_file())
            self.assertTrue((project / "00_handoff/source-bundles/HO002-r2/manifest.yaml").is_file())
            self.assertTrue((project / "03_plan/history/HO002-r1/production-plan.yaml").is_file())
            self.assertEqual(validate_project(project, ROOT), [])
            impact = load_yaml(project / "00_handoff/impact-reports/HO002-r2.yaml")
            requirement_change = next(item for item in impact["changes"] if item["kind"] == "requirements")
            self.assertEqual(requirement_change["status"], "CHANGED")
            self.assertIn("RQ001", requirement_change["old_ids"])
            self.assertTrue(requirement_change["affected"]["task_ids"])
            before = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file() and ".handoff-revision.lock" not in str(path)}
            self.assertEqual(new_production_main(args), 0)
            after = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file() and ".handoff-revision.lock" not in str(path)}
            self.assertEqual(before, after)

    def test_initial_history_migration_keeps_handoff_validated_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            self.assertEqual(new_production_main(["revision-test", "--handoff", str(FIXTURE), "--output-root", str(output)]), 0)
            project = output / "production/revision-test"
            candidate = _revision_bundle(root / "candidate")
            result = new_production_main([
                "revision-test", "--handoff", str(candidate), "--output-root", str(output), "--accept-revision",
                "--occurred-at", "2026-08-25T12:01:00+09:00", "--actor-kind", "SYSTEM", "--actor-id", "migration/test",
                "--idempotency-key", "handoff/migration/HO002/r2",
            ])
            self.assertEqual(result, 0)
            self.assertEqual(Runtime(project, ROOT).replay()["state"], "HANDOFF_VALIDATED")
            events = [event for _, event in load_jsonl(project / "00_handoff/handoff-log.jsonl")]
            self.assertEqual([event["type"] for event in events], ["HANDOFF_ACCEPTED", "HANDOFF_REVISION_ACCEPTED"])
            self.assertEqual(validate_project(project, ROOT), [])

    def test_revision_rejects_lineage_without_changing_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._prepared_project(root)
            candidate = _revision_bundle(root / "candidate", supersedes="HO999")
            before = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file() and ".handoff-revision.lock" not in str(path)}
            result = new_production_main(["revision-test", "--handoff", str(candidate), "--output-root", str(root / "output"), "--accept-revision", "--occurred-at", "2026-08-25T12:01:00+09:00", "--actor-kind", "HUMAN", "--actor-id", "human/test", "--idempotency-key", "handoff/bad/r2"])
            self.assertNotEqual(result, 0)
            after = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file() and ".handoff-revision.lock" not in str(path)}
            self.assertEqual(before, after)

    def test_revision_rejects_same_identity_with_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._prepared_project(root)
            candidate = _revision_bundle(root / "candidate", revision=1, supersedes=None)
            before = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file() and ".handoff-revision.lock" not in str(path)}
            result = new_production_main([
                "revision-test", "--handoff", str(candidate), "--output-root", str(root / "output"), "--accept-revision",
                "--occurred-at", "2026-08-25T12:01:00+09:00", "--actor-kind", "HUMAN", "--actor-id", "human/test",
                "--idempotency-key", "handoff/conflict/r1",
            ])
            self.assertNotEqual(result, 0)
            after = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file() and ".handoff-revision.lock" not in str(path)}
            self.assertEqual(before, after)

    def test_revision_build_failure_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._prepared_project(root)
            candidate = _revision_bundle(root / "candidate")
            before = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file() and ".handoff-revision.lock" not in str(path)}
            with patch("tools.build_plan.main", return_value=1):
                result = new_production_main([
                    "revision-test", "--handoff", str(candidate), "--output-root", str(root / "output"), "--accept-revision",
                    "--occurred-at", "2026-08-25T12:01:00+09:00", "--actor-kind", "HUMAN", "--actor-id", "human/test",
                    "--idempotency-key", "handoff/failure/r2",
                ])
            self.assertNotEqual(result, 0)
            after = {path.relative_to(project).as_posix(): path.read_bytes() for path in project.rglob("*") if path.is_file() and ".handoff-revision.lock" not in str(path)}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
