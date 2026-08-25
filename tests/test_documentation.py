from __future__ import annotations

import unittest
from pathlib import Path

from tools.lib.yaml_io import load_yaml


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class DocumentationContractTests(unittest.TestCase):
    def test_startup_operations_and_schema_guides_exist(self) -> None:
        for name in ("agent-startup.md", "operations-runbook.md", "schema-reference.md", "release-gate.md"):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_guides_use_supported_cli_and_keep_repository_boundary(self) -> None:
        guides = "\n".join((DOCS / name).read_text(encoding="utf-8") for name in ("agent-startup.md", "operations-runbook.md", "schema-reference.md", "release-gate.md"))
        self.assertNotIn("tools/" + "run_project.py", guides)
        self.assertNotIn("/Users/", guides)
        self.assertNotIn("/private/", guides)
        for command in (
            "tools/new_production.py",
            "tools/build_plan.py",
            "tools/build_prototype.py",
            "tools/run_runtime.py",
            "tools/run_execution.py",
            "tools/build_result.py",
            "tools/export_result.py",
            "tools/run_evaluation.py",
            "tools/run_release_gate.py",
            "tools/validate.py",
        ):
            self.assertIn(command, guides)
        for safety_term in ("外部effect", "UNKNOWN", "PRIVATE_RAW", "approval", "HUMAN_APPROVAL_REQUIRED"):
            self.assertIn(safety_term, guides)

    def test_schema_reference_matches_registry(self) -> None:
        registry = load_yaml(ROOT / "config/schema-registry.yaml")
        entries = registry["schemas"]
        self.assertEqual(len(entries), len({entry["id"] for entry in entries}))
        for entry in entries:
            path = ROOT / entry["path"]
            self.assertTrue(path.is_file(), entry["id"])
        reference = (DOCS / "schema-reference.md").read_text(encoding="utf-8")
        for schema_id in ("production-project", "production-plan", "prototype-control", "runtime-event", "runtime-state", "output-version", "quality-result", "installation-result", "observation-record", "observations", "production-result"):
            self.assertIn(schema_id, reference)


if __name__ == "__main__":
    unittest.main()
