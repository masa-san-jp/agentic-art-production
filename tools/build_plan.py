#!/usr/bin/env python3
"""Build a deterministic production plan from an accepted handoff project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.canonical import canonical_sha256
from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.planning import validate_plan_document
from tools.lib.plan_derivation import build_plan as build_generic_plan
from tools.lib.yaml_io import dump_yaml, load_json, load_yaml


ARTIFACT_FILES = {
    "requirements": "production-requirements.yaml",
    "hypotheses": "production-hypotheses.yaml",
    "prototype_plans": "prototype-plans.yaml",
    "acceptance_tests": "acceptance-tests.yaml",
    "source_refs": "source-ref-index.yaml",
}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _require_mapping(path: Path) -> dict[str, Any]:
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise DiagnosticError(_finding("PLANNING_INPUT_OBJECT", "planning input must be a YAML mapping", file=path, remediation="Restore the accepted handoff artifact as a mapping."))
    return value


def _records(value: dict[str, Any], key: str, path: Path) -> list[dict[str, Any]]:
    records = value.get(key, [])
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise DiagnosticError(_finding("PLANNING_INPUT_RECORDS", f"{key} must be a list of objects", file=path, location=f"/{key}", remediation="Regenerate the research handoff artifact with its declared record collection."))
    return sorted(records, key=lambda record: str(record.get("id", "")))


def _load_inputs(project_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    handoff_path = project_root / "00_handoff/production-handoff.yaml"
    manifest_path = project_root / "00_handoff/source-bundle-manifest.yaml"
    bundle_root = project_root / "00_handoff/source-bundle"
    handoff = _require_mapping(handoff_path)
    bundle_manifest = _require_mapping(manifest_path)
    if not bundle_root.is_dir():
        raise DiagnosticError(_finding("PLANNING_BUNDLE_MISSING", "accepted project does not contain its source bundle", file=bundle_root, remediation="Re-accept a self-contained handoff before building a plan."))
    artifacts: dict[str, dict[str, Any]] = {}
    for name, filename in ARTIFACT_FILES.items():
        artifacts[name] = _require_mapping(bundle_root / "artifacts" / filename)
    source_input = {"handoff": handoff, "bundle_manifest": bundle_manifest, "artifacts": artifacts}
    return handoff, bundle_manifest, source_input, artifacts


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_contexts(project_root: Path, plan: dict[str, Any]) -> None:
    context_root = project_root / "03_plan/agent-contexts"
    common = f"""# Production context\n\nThis context is generated from `{plan['plan_id']}` and must be used with the accepted handoff. It does not authorize external effects.\n\n- Project: `{plan['project_id']}`\n- Plan: `{plan['plan_id']}` revision {plan['plan_revision']}\n- State: `{plan['state']}`\n- Handoff: `{plan['handoff_ref']['id']}` revision {plan['handoff_ref']['revision']}\n"""
    contexts = {
        "planning-agent.md": common + "\n## Allowed scope\n\nRead-only validation of plan references, coverage, dependency order, and documented gaps.\n\n## Tasks\n\n" + (_markdown_bullets([f"`{task['id']}` — {task['title']} ({task['status']})" for task in plan["tasks"] if task["effect_type"] in {"READ_ONLY", "REPOSITORY_WRITE"}]) if any(task["effect_type"] in {"READ_ONLY", "REPOSITORY_WRITE"} for task in plan["tasks"]) else "- No read-only task was derived from the accepted handoff.\n"),
        "prototype-agent.md": common + "\n## Gate\n\nOnly tasks explicitly present in the accepted prototype plan are listed below. External or physical effects remain blocked until their approval requirement is recorded.\n\n## Tasks\n\n" + (_markdown_bullets([f"`{task['id']}` — {task['title']} ({task['status']})" for task in plan["tasks"] if task["effect_type"] not in {"READ_ONLY", "REPOSITORY_WRITE"}]) if any(task["effect_type"] not in {"READ_ONLY", "REPOSITORY_WRITE"} for task in plan["tasks"]) else "- No physical or external task was derived from the accepted handoff.\n"),
        "review-agent.md": common + "\n## Gate\n\nReview only the acceptance tests and evidence references present in the accepted handoff.\n\n## Acceptance tests\n\n" + (_markdown_bullets([f"`{test['id']}` — {test['pass_condition']}" for test in plan["acceptance_tests"]]) if plan["acceptance_tests"] else "- No acceptance test is attached to a mandatory requirement.\n"),
    }
    for filename, text in contexts.items():
        (context_root / filename).parent.mkdir(parents=True, exist_ok=True)
        (context_root / filename).write_text(text, encoding="utf-8")


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "未設定"
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if isinstance(value, dict):
        return ", ".join(f"{key}={_markdown_cell(item)}" for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(_markdown_cell(item) for item in value) or "なし"
    return str(value).replace("|", r"\|").replace("\n", "<br>")


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_markdown_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines) if rows else "該当なし。"


def _markdown_bullets(values: list[Any]) -> str:
    return "\n".join(f"- {_markdown_cell(value)}" for value in values) if values else "- なし"


def _render_human_plan(project_root: Path, plan: dict[str, Any]) -> str:
    """Render the one complete production plan intended for human producers."""
    handoff = _require_mapping(project_root / "00_handoff/production-handoff.yaml")
    bundle_root = project_root / "00_handoff/source-bundle"
    requirements_path = bundle_root / "artifacts/production-requirements.yaml"
    hypotheses_path = bundle_root / "artifacts/production-hypotheses.yaml"
    requirements = _records(_require_mapping(requirements_path), "requirements", requirements_path)
    hypotheses = _records(_require_mapping(hypotheses_path), "hypotheses", hypotheses_path)
    selected_hypothesis = next(
        item for item in hypotheses if item.get("id") == plan["selection_record"]["selected_hypothesis_id"]
    )
    coverage_by_id = {str(item["requirement_id"]): item for item in plan["coverage_report"]["requirements"]}
    approval_requirements = plan["approval_register"]["requirements"]
    critical_path = " → ".join(f"`{task_id}`" for task_id in plan["critical_path_task_ids"]) or "未設定"
    ready_tasks = [task["id"] for task in plan["tasks"] if task.get("status") == "READY"]
    gated_tasks = [task["id"] for task in plan["tasks"] if task.get("effect_type") not in {"READ_ONLY", "REPOSITORY_WRITE"}]

    lines = [
        "# 統合制作計画書",
        "",
        "> この文書は、受理済みhandoffと検証済みの制作計画を、人間が読んで制作判断・制作実務に使える一つの計画書へ統合したものです。計画の生成は、購入・契約・公開・連絡・削除・物理作業の実行承認を意味しません。",
        "",
        "## 1. 文書概要",
        "",
        _markdown_table(["項目", "内容"], [
            ["プロジェクト", plan["project_id"]],
            ["計画", f"{plan['plan_id']} revision {plan['plan_revision']}"],
            ["計画状態", plan["state"]],
            ["生成日時", plan["generated_at"]],
            ["handoff", f"{plan['handoff_ref']['id']} revision {plan['handoff_ref']['revision']}"],
            ["要件カバレッジ", f"{plan['coverage_report']['coverage_percent']}%"],
            ["クリティカルパス", critical_path],
        ]),
        "",
        "## 2. 制作目的と採択内容",
        "",
        _markdown_table(["項目", "内容"], [
            ["採択仮説", selected_hypothesis.get("id")],
            ["仮説タイトル", selected_hypothesis.get("title")],
            ["仮説", selected_hypothesis.get("proposition")],
            ["仮説状態", selected_hypothesis.get("status")],
            ["選択状態", plan["selection_record"]["status"]],
            ["選択権限", plan["selection_record"]["authority"]],
            ["人間承認要否", plan["selection_record"]["human_approval_required"]],
            ["選択理由", plan["selection_record"]["rationale"]],
            ["創作指針", handoff.get("creative_direction_ref")],
        ]),
        "",
        "## 3. 要件と受入の目的",
        "",
        _markdown_table(["要件", "優先度", "要件内容", "出所", "受入テスト", "計画上の対応"], [
            [
                requirement.get("id"), requirement.get("priority"), requirement.get("statement"),
                requirement.get("source_decision_ids"), requirement.get("acceptance_test_ids"),
                coverage_by_id.get(str(requirement.get("id")), {}).get("status", "未確認"),
            ]
            for requirement in requirements
        ]),
        "",
        "## 4. 制作範囲と成果物",
        "",
        _markdown_table(["項目", "内容"], [
            ["スコープ状態", plan["scope_baseline"]["status"]],
            ["必須要件ID", plan["scope_baseline"]["mandatory_requirement_ids"]],
            ["試作計画ID", plan["scope_baseline"]["prototype_plan_ids"]],
            ["前提", [assumption["statement"] for assumption in plan["assumptions"]]],
            ["除外・未許可範囲", plan["scope_baseline"]["excluded_scope"]],
            ["権利制約", handoff.get("constraints", {}).get("rights", [])],
            ["安全制約", handoff.get("constraints", {}).get("safety", [])],
            ["プライバシー制約", handoff.get("constraints", {}).get("privacy", [])],
            ["再計画トリガー", handoff.get("replan_triggers", [])],
        ]),
        "",
        _markdown_table(["成果物", "種別", "内容", "受入テスト", "担当能力", "納期", "状態"], [
            [
                item["id"], item["type"], item["title"], item["acceptance_test_ids"],
                item["owner_capability"], item["due_milestone_id"], item["status"],
            ]
            for item in plan["deliverables"]
        ]),
        "",
        "## 5. 技術仕様・材料・資源",
        "",
        _markdown_table(["仕様", "対象", "目標", "許容差", "測定方法", "出所要件", "状態"], [
            [item["id"], item["parameter"], item["target"], item["tolerance"], item["measurement_method"], item["source_requirement_ids"], item["status"]]
            for item in plan["technical_specifications"]
        ]),
        "",
        _markdown_table(["材料", "仕様", "数量", "権利", "安全", "出所試作", "状態"], [
            [item["id"], item["name"] + " — " + item["specification"], item["quantity"], item["rights_status"], item["safety_status"], item["source_prototype_plan_ids"], item["status"]]
            for item in plan["materials"]
        ]),
        "",
        _markdown_table(["資源", "種別", "必要能力", "数量", "可用性", "関連タスク"], [
            [item["id"], item["type"], item["capability"], item["quantity"], item["availability"], item["source_task_ids"]]
            for item in plan["resources"]
        ]),
        "",
        "## 6. 工程と作業手順",
        "",
        _markdown_table(["作業パッケージ", "内容", "成果物", "タスク", "担当能力", "状態"], [
            [item["id"], item["title"], item["deliverable_ids"], item["task_ids"], item["owner_capability"], item["status"]]
            for item in plan["work_packages"]
        ]),
        "",
        _markdown_table(["タスク", "作業", "前提", "所要時間", "必要材料", "受入条件", "効果種別", "承認", "状態"], [
            [
                item["id"], item["title"], item["depends_on"], item["duration"], item["required_material_ids"],
                item["acceptance_condition"], item["effect_type"], item["approval_requirement_ids"], item["status"],
            ]
            for item in plan["tasks"]
        ]),
        "",
        f"**実施順の読み方:** クリティカルパスは {critical_path} です。READYタスクは {_markdown_cell(ready_tasks)}、承認境界の対象タスクは {_markdown_cell(gated_tasks)} です。",
        "",
        "## 7. 試作・受入評価",
        "",
        _markdown_table(["テスト", "対象要件", "方法", "合格条件", "現在結果"], [
            [item["id"], item["target_requirement"], item["method"], item["pass_condition"], item["result"]]
            for item in plan["acceptance_tests"]
        ]),
        "",
        _markdown_table(["マイルストーン", "内容", "順序", "前提", "状態"], [
            [item["id"], item["title"], item["sequence"], item["depends_on"], item["status"]]
            for item in plan["schedule"]["milestones"]
        ]),
        "",
        "## 8. 日程と予算",
        "",
        _markdown_table(["日程・予算項目", "内容"], [
            ["日程モード", plan["schedule"]["mode"]],
            ["日程基準線", plan["schedule"]["baseline_status"]],
            ["タスク日程", plan["schedule"]["task_schedule"]],
            ["日程ギャップ", plan["schedule"]["gaps"]],
            ["通貨", plan["budget"]["currency"]],
            ["予算総額", plan["budget"]["baseline_total"]],
            ["予備費", plan["budget"]["contingency"]],
            ["承認閾値", plan["budget"]["approval_threshold"]],
            ["予算状態", plan["budget"]["status"]],
            ["予算ギャップ", plan["budget"]["gaps"]],
        ]),
        "",
        _markdown_table(["予算項目", "区分", "内容", "金額", "根拠", "確度", "状態"], [
            [item["id"], item["category"], item["description"], item["amount"], item["basis"], item["confidence"], item["status"]]
            for item in plan["budget"]["items"]
        ]),
        "",
        "## 9. リスクと未解決事項",
        "",
        _markdown_table(["リスク", "内容", "影響", "軽減策", "重要度", "可能性", "担当", "状態"], [
            [item["id"], item["title"], item["impact"], item["mitigation"], item["severity"], item["likelihood"], item["owner_capability"], item["status"]]
            for item in plan["risks"]
        ]),
        "",
        _markdown_table(["ギャップ", "内容", "ブロッキング"], [
            [item["id"], item["statement"], item["blocking"]] for item in plan["gaps"]
        ]),
        "",
        "## 10. 承認・安全境界",
        "",
        _markdown_table(["承認ID", "対象行為", "対象", "対象hash", "権限者", "状態", "理由", "関連タスク"], [
            [item["id"], item["action"], item["target_ref"], item["target_sha256"], item["authority"], item["status"], item["reason"], item["task_ids"]]
            for item in approval_requirements
        ]),
        "",
        "この計画書は、明示的な人間承認が記録されるまで、物理作業、外部サービスへの接続、購入、契約、支払い、公開、応募、連絡、削除を許可しません。材料の権利・安全状態、会場条件、担当能力、見積、日程は制作開始前に人間が確認してください。",
        "",
        "## 11. 人間向け実行前チェックリスト",
        "",
        _markdown_bullets([
            "採択仮説と要件の内容・優先度を確認する。",
            "未設定の会場、照明、日程、予算、見積、担当能力を確定する。",
            "材料の権利状態と安全状態を確認し、変更時は再評価する。",
            "物理・外部効果タスクの対象・範囲・hashを確認して承認する。",
            "各受入テストの実施条件と証跡の保存先を決める。",
            "制作中の差分・失敗・変更要求を既存の計画に上書きせず記録する。",
        ]),
        "",
        "## 12. 証跡と再現性",
        "",
        _markdown_table(["項目", "値"], [
            ["handoff content hash", plan["handoff_ref"]["content_sha256"]],
            ["plan integrity hash", plan["integrity"]["content_sha256"]],
            ["source input hash", plan["determinism"]["source_input_sha256"]],
            ["生成アルゴリズム", plan["determinism"]["algorithm"]],
            ["トレーサビリティID", plan["selection_record"]["trace_refs"]],
            ["依存グラフ", plan["dependency_graph"]],
        ]),
        "",
        "### 受け渡し時の注意",
        "",
        "ユーザーに渡す計画書はこの `03_plan/production-plan.md` 一つです。`production-plan.yaml`などの構造化ファイルと`agent-contexts/`は、検証・再生成・内部運用のためにGit外の制作projectへ保持されます。完成作品、RAW、動画、音声、3D、大容量asset、credential、signed URLはこの計画書へ埋め込みません。",
        "",
    ]
    return "\n".join(lines)


def _write_outputs(project_root: Path, plan: dict[str, Any]) -> None:
    legacy_brief = project_root / "03_plan/human-brief.md"
    if legacy_brief.is_file():
        raise DiagnosticError(_finding(
            "PLANNING_LEGACY_OUTPUT",
            "legacy human-brief.md exists and cannot be silently replaced",
            file=legacy_brief,
            remediation="Move or archive the legacy brief, then regenerate the single integrated production-plan.md.",
        ))
    human_plan = _render_human_plan(project_root, plan)
    output = {
        "01_scope/selection-record.yaml": plan["selection_record"],
        "01_scope/scope-baseline.yaml": plan["scope_baseline"],
        "01_scope/assumptions-register.yaml": {"assumptions": plan["assumptions"]},
        "02_specification/deliverables.yaml": {"deliverables": plan["deliverables"]},
        "02_specification/technical-specifications.yaml": {"technical_specifications": plan["technical_specifications"]},
        "02_specification/acceptance-tests.yaml": {"acceptance_tests": plan["acceptance_tests"]},
        "02_specification/material-register.yaml": {"materials": plan["materials"]},
        "02_specification/asset-register.yaml": {"assets": []},
        "03_plan/production-plan.yaml": plan,
        "03_plan/work-packages.yaml": {"work_packages": plan["work_packages"]},
        "03_plan/task-plan.yaml": {"tasks": plan["tasks"]},
        "03_plan/schedule.yaml": plan["schedule"],
        "03_plan/budget.yaml": plan["budget"],
        "03_plan/resource-plan.yaml": {"resources": plan["resources"]},
        "03_plan/procurement-plan.yaml": {"status": "NOT_AUTHORIZED", "candidates": [], "reason": "No purchase or supplier action is authorized at planning stage."},
        "03_plan/requirement-coverage.yaml": plan["coverage_report"],
        "07_governance/approval-register.yaml": plan["approval_register"],
        "07_governance/risk-register.yaml": {"risks": plan["risks"]},
    }
    for relative, value in output.items():
        dump_yaml(value, project_root / relative)
    (project_root / "03_plan/production-plan.md").write_text(human_plan, encoding="utf-8")
    _write_contexts(project_root, plan)

    manifest_path = project_root / "manifest.yaml"
    manifest = _require_mapping(manifest_path)
    manifest["state"] = "PLANNING"
    dump_yaml(manifest, manifest_path)
    state_path = project_root / "08_runtime/production-state.json"
    state = load_json(state_path)
    state.pop("state_sha256", None)
    state["state"] = "PLANNING"
    state["revision"] = 1
    state["plan_id"] = plan["plan_id"]
    state["state_sha256"] = canonical_sha256(state)
    _json_write(state_path, state)
    _json_write(project_root / "08_runtime/dependency-index.json", {"schema_version": "1.0.0", "nodes": plan["dependency_graph"]["nodes"], "edges": plan["dependency_graph"]["edges"]})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path, help="accepted Git-external production project")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = repository_root()
    try:
        project_root = args.project_root.resolve()
        plan = build_generic_plan(project_root)
        findings = validate_plan_document(plan, repository=repository, plan_path=project_root / "03_plan/production-plan.yaml")
        if findings:
            emit_findings(findings, output_format=args.format)
            return EXIT_VALIDATION
        _write_outputs(project_root, plan)
        print(str(project_root / "03_plan/production-plan.md"))
        return EXIT_SUCCESS
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format=args.format)
        return EXIT_VALIDATION
    except (OSError, KeyError, TypeError, ValueError) as exc:
        emit_findings([_finding("PLANNING_BUILD", str(exc), file=args.project_root, remediation="Correct the accepted project input and retry plan generation.")], output_format=args.format)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
