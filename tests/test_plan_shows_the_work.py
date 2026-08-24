from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_plan import main as build_plan_main
from tools.lib.execution import ExecutionManager
from tools.lib.yaml_io import load_yaml
from tools.new_production import main as new_production_main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "000557bfabd40000000049454e44ae426082"
)


class PlanShowsTheWorkTests(unittest.TestCase):
    def _project(self, directory: str) -> Path:
        output_root = Path(directory) / "output"
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
        return output_root / "production/smoke"

    @staticmethod
    def _output(revision: int, status: str, quality_ids: list[str], *, previews: list[dict] | None = None) -> dict:
        record = {
            "schema_version": "1.0.0",
            "output_id": "OUT001",
            "revision": revision,
            "project_id": "production/smoke",
            "deliverable_id": "DL001",
            "version": f"1.0.{revision}",
            "asset_ref": {
                "asset_id": "AS001",
                "uri": "urn:asset:synthetic:OUT001",
                "version": f"1.0.{revision}",
                "sha256": "sha256:" + "1" * 64,
                "media_type": "image/png",
                "rights_status": "PROJECT_INTERNAL",
            },
            "status": status,
            "source_task_ids": ["TK001"],
            "quality_result_ids": quality_ids,
            "created_at": "2026-08-12T12:00:00+09:00",
            "created_by": {"kind": "AGENT", "id": "test-agent"},
            "supersedes": {"output_id": "OUT001", "revision": 1} if revision > 1 else None,
        }
        if previews is not None:
            record["previews"] = previews
        return record

    @staticmethod
    def _quality() -> dict:
        return {
            "schema_version": "1.0.0",
            "quality_id": "QL001",
            "revision": 1,
            "project_id": "production/smoke",
            "output_id": "OUT001",
            "output_revision": 1,
            "status": "PASS",
            "dimensions": [{"id": "QD001", "criterion": "traceable synthetic output", "result": "PASS", "method": "fixture inspection"}],
            "evidence_refs": ["urn:evidence:synthetic:QL001"],
            "external_validation_status": "NOT_REQUIRED",
            "executed_at": "2026-08-12T12:01:00+09:00",
            "created_at": "2026-08-12T12:01:00+09:00",
            "created_by": {"kind": "AGENT", "id": "test-agent"},
        }

    def _make_available_output(self, manager: ExecutionManager, *, previews: list[dict] | None) -> None:
        manager.record_output(self._output(1, "CANDIDATE", []), occurred_at="2026-08-12T12:00:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="output/OUT001/1")
        manager.record_quality(self._quality(), occurred_at="2026-08-12T12:01:00+09:00", actor_kind="AGENT", actor_id="test-agent", idempotency_key="quality/QL001/1")
        manager.record_output(
            self._output(2, "AVAILABLE", ["QL001"], previews=previews),
            occurred_at="2026-08-12T12:02:00+09:00",
            actor_kind="AGENT",
            actor_id="test-agent",
            idempotency_key="output/OUT001/2",
        )

    def test_prototype_inputs_and_constraints_reach_the_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(directory)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            self.assertEqual(plan["work_packages"][0]["input_ids"], ["Synthetic fixture data"])
            rendered = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
            self.assertIn("Synthetic fixture data", rendered)
            self.assertIn("Do not perform physical work automatically.", rendered)
            # A task with no material register entry points at its work package instead of claiming "none".
            self.assertIn("WP001の投入物", rendered)

    def test_plan_without_a_made_output_says_nothing_is_made_yet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(directory)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            rendered = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
            self.assertIn("## 6. できている物", rendered)
            self.assertIn("まだ何も作っていない", rendered)

    def test_plan_shows_the_made_output_and_embeds_its_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(directory)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            manager = ExecutionManager(project, ROOT)
            manager.init()
            preview = project / "05_execution/previews/OUT001-far.png"
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_bytes(PNG_1PX)
            self._make_available_output(manager, previews=[{"path": "05_execution/previews/OUT001-far.png", "caption": "4mから見たとき"}])
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            rendered = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
            self.assertIn("## 6. できている物", rendered)
            self.assertNotIn("まだ何も作っていない", rendered)
            self.assertIn("![OUT001](05_execution/previews/OUT001-far.png)", rendered)
            self.assertIn("*4mから見たとき*", rendered)
            self.assertIn("urn:asset:synthetic:OUT001", rendered)
            self.assertIn("image/png", rendered)
            self.assertIn("現物で確かめたこと", rendered)
            self.assertIn("traceable synthetic output", rendered)
            self.assertIn("fixture inspection", rendered)

    def test_declared_preview_that_is_missing_is_reported_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(directory)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            manager = ExecutionManager(project, ROOT)
            manager.init()
            self._make_available_output(manager, previews=[{"path": "05_execution/previews/OUT001-gone.png", "caption": "消えた版"}])
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            rendered = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
            self.assertIn("05_execution/previews/OUT001-gone.png", rendered)
            self.assertIn("プレビュー画像が見つからない", rendered)
            self.assertNotIn("![OUT001](", rendered)

    def test_sections_after_the_made_work_keep_their_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(directory)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            rendered = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
            headings = [line for line in rendered.splitlines() if line.startswith("## ")]
            self.assertEqual(
                headings,
                [
                    "## 1. 完成像",
                    "## 2. テーマ",
                    "## 3. メッセージ",
                    "## 4. コンセプト",
                    "## 5. 調査の要約",
                    "## 6. できている物",
                    "## 7. 制作範囲と成果物",
                    "## 8. 技術仕様・材料・資源",
                    "## 9. 工程と作業手順",
                    "## 10. 試作・受入評価",
                    "## 11. 日程と予算",
                    "## 12. リスクと未解決事項",
                    "## 13. 承認・安全境界",
                    "## 14. 人間向け実行前チェックリスト",
                    "## 15. 証跡と再現性",
                ],
            )


if __name__ == "__main__":
    unittest.main()
