from __future__ import annotations

import shutil
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

from tools.lib.bundle import open_bundle
from tools.lib.canonical import canonical_json_bytes, canonical_sha256, result_sha256
from tools.lib.config import load_config
from tools.lib.diagnostics import DiagnosticError, Finding
from tools.lib.schema import load_schema, validate_instance
from tools.lib.security import validate_asset_uri
from tools.lib.yaml_io import dump_yaml, load_jsonl, load_yaml
from tools.new_production import main as new_production_main
from tools.build_plan import main as build_plan_main
from tools.build_prototype import main as build_prototype_main
from tools.lib.planning import validate_plan_document
from tools.lib.prototype import validate_prototype_document
from tools.lib.runtime import Runtime
from tools.validate import validate_project, validate_repository


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/handoff/minimal"


class BootstrapContractTests(unittest.TestCase):
    def test_canonical_json_reference_vector(self) -> None:
        value = {"b": 2, "a": "あ"}
        self.assertEqual(canonical_json_bytes(value), b'{"a":"\xe3\x81\x82","b":2}')
        self.assertEqual(canonical_sha256(value), "sha256:a96b206e3cae20b94cf1d456985040a3696932025829f6caa19f650ce1c12ba2")

    def test_canonical_json_rejects_float_and_decimal_values(self) -> None:
        with self.assertRaises(TypeError):
            canonical_json_bytes({"amount": 1.0})
        with self.assertRaises(TypeError):
            canonical_json_bytes({"amount": Decimal("1.00")})

    def test_config_vocabulary_uri_and_diagnostic_contracts(self) -> None:
        units = load_config(ROOT, "units.yaml")
        self.assertEqual(units["units"]["mm"]["dimension"], "length")
        self.assertNotIn("inch", units["units"])
        self.assertIsNone(validate_asset_uri("urn:asset:AS001", allowed_schemes=("urn", "https")))
        self.assertEqual(
            validate_asset_uri("https://assets.example/AS001?token=secret", allowed_schemes=("urn", "https")).rule,
            "ASSET_URI_QUERY",
        )
        diagnostic = Finding("TEST_RULE", "example", file="fixture.yaml", remediation="fix it").as_dict()
        diagnostic_schema = load_schema(ROOT / "schemas/diagnostic.schema.json")
        self.assertEqual(validate_instance(diagnostic, diagnostic_schema, schema_path=ROOT / "schemas/diagnostic.schema.json"), [])

    def test_schema_common_id_from_research_bundle_is_resolved_offline(self) -> None:
        research_common_id = "https://example.invalid/agentic-art-research/common.schema.json"
        common = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": research_common_id,
            "$defs": {"nonEmptyString": {"type": "string", "minLength": 1}},
        }
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.invalid/test/handoff.schema.json",
            "type": "object",
            "properties": {"value": {"$ref": research_common_id + "#/$defs/nonEmptyString"}},
            "required": ["value"],
        }
        self.assertEqual(
            validate_instance(
                {"value": "resolved from the bundle"},
                schema,
                schema_path=ROOT / "schemas/diagnostic.schema.json",
                common_schema=common,
            ),
            [],
        )

    def test_duplicate_yaml_keys_are_rejected(self) -> None:
        with self.assertRaises(DiagnosticError) as fixture_error:
            load_yaml(ROOT / "tests/fixtures/invalid/duplicate-key.yaml")
        self.assertEqual(fixture_error.exception.finding.rule, "YAML_DUPLICATE_KEY")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.yaml"
            path.write_text("one: 1\none: 2\n", encoding="utf-8")
            with self.assertRaises(DiagnosticError) as raised:
                load_yaml(path)
            self.assertEqual(raised.exception.finding.rule, "YAML_DUPLICATE_KEY")

    def test_jsonl_requires_one_object_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text('{"id":"A"}\n{"id":"B"}\n', encoding="utf-8")
            self.assertEqual(len(load_jsonl(path)), 2)
            path.write_text('{"id":"A"}\n\n', encoding="utf-8")
            with self.assertRaises(DiagnosticError) as raised:
                load_jsonl(path)
            self.assertEqual(raised.exception.finding.rule, "JSONL_BLANK_LINE")

    def test_repository_contracts_validate(self) -> None:
        self.assertEqual(validate_repository(ROOT), [])

    def test_external_schema_snapshot_is_registered_and_valid(self) -> None:
        registry = load_config(ROOT, "schema-registry.yaml")
        handoff_entry = next(item for item in registry["schemas"] if item["id"] == "production-handoff.v1")
        self.assertEqual(handoff_entry["status"], "LOCAL")
        self.assertEqual(handoff_entry["version"], "1.0.0")
        self.assertEqual(handoff_entry["source_commit"], "aba5f1738cc0066c994433d91c333b3cfe5210da")
        self.assertEqual(handoff_entry["acquired_at"], "2026-08-12T06:54:36+09:00")
        self.assertEqual(handoff_entry["sha256"], "sha256:715f2426474de9d957ef3129e0a65d69492ff7e75181272b52cb4cbb850cf0f7")
        self.assertTrue((ROOT / handoff_entry["path"]).is_file())
        load_schema(ROOT / handoff_entry["path"])

    def test_production_result_schema_matches_research_consumer_contract(self) -> None:
        schema = load_schema(ROOT / "schemas/production-result.schema.json")
        result = {
            "schema_version": "1.0.0",
            "result_id": "PR001",
            "production_project_id": "production/minimal",
            "production_commit": "0123456789abcdef0123456789abcdef01234567",
            "generated_at": "2026-08-11T16:00:00+09:00",
            "accepted_handoff": {
                "id": "HO001",
                "content_sha256": "sha256:" + "1" * 64,
                "research_project_id": "project/minimal",
                "research_commit": "0123456789abcdef0123456789abcdef01234567",
            },
            "selection": {"selected_hypothesis_id": "PH001", "authority": "HUMAN", "approval_ref": "AP001"},
            "outputs": [],
            "test_results": [{
                "acceptance_test_id": "AT001",
                "result": "PASS",
                "executed_at": "2026-08-11T16:00:00+09:00",
                "conditions": "synthetic fixture",
                "evidence_ref": "urn:production:test:AT001",
            }],
            "observations": [{
                "id": "OB001",
                "statement": "The repeated element remained observable.",
                "method": "fixture-field-review",
                "limitations": "Synthetic fixture only.",
                "related_requirement_ids": ["RQ001"],
            }],
            "deviations": [{"id": "DEV001", "statement": "Synthetic fixture deviation."}],
            "incidents": [{"id": "INC001", "severity": "CRITICAL", "statement": "Synthetic fixture incident."}],
            "research_change_requests": [],
            "open_gaps": [],
            "integrity": {"content_sha256": result_sha256({})},
        }
        result["integrity"] = {"content_sha256": result_sha256(result)}
        self.assertEqual([], validate_instance(result, schema, schema_path=ROOT / "schemas/production-result.schema.json"))

    def test_directory_and_zip_bundles_are_accepted(self) -> None:
        with open_bundle(FIXTURE, ROOT) as bundle:
            self.assertEqual(bundle.handoff["handoff_id"], "HO001")
            self.assertEqual(bundle.handoff["integrity"]["content_sha256"], "sha256:268ad173adfa9ba13e3c76e10e3ac3171341196c4ad1756f45c12db45e9d98fa")
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "handoff.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for path in FIXTURE.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(FIXTURE).as_posix())
            with open_bundle(archive, ROOT) as bundle:
                self.assertEqual(bundle.handoff["revision"], 1)

    def test_synchronized_filesystem_metadata_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle_path = Path(directory) / "bundle"
            shutil.copytree(FIXTURE, bundle_path)
            for parent in (bundle_path, bundle_path / "artifacts", bundle_path / "schemas"):
                (parent / "Icon\r").write_bytes(b"")
            with open_bundle(bundle_path, ROOT) as bundle:
                self.assertEqual(bundle.handoff["handoff_id"], "HO001")

    def test_tampered_bundle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tampered = Path(directory) / "handoff"
            shutil.copytree(FIXTURE, tampered)
            handoff = tampered / "production-handoff.yaml"
            handoff.write_text(handoff.read_text(encoding="utf-8").replace("venue_changed", "venue_changed_again"), encoding="utf-8")
            with self.assertRaises(DiagnosticError) as raised:
                with open_bundle(tampered, ROOT):
                    pass
            self.assertEqual(raised.exception.finding.rule, "MANIFEST_FILE_HASH")

    def test_new_production_is_external_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(validate_project(project, ROOT), [])
            manifest_before = (project / "manifest.yaml").read_bytes()
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            self.assertEqual((project / "manifest.yaml").read_bytes(), manifest_before)

            manifest = load_yaml(project / "manifest.yaml")
            manifest["handoff"]["content_sha256"] = "sha256:" + "0" * 64
            dump_yaml(manifest, project / "manifest.yaml")
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 1)

    def test_repository_output_root_is_rejected(self) -> None:
        self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(ROOT)]), 1)

    def test_deterministic_planning_build_materializes_external_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            first = (project / "03_plan/production-plan.yaml").read_bytes()
            human_plan = project / "03_plan/production-plan.md"
            first_human_plan = human_plan.read_bytes()
            self.assertTrue(human_plan.is_file())
            self.assertFalse((project / "03_plan/human-brief.md").exists())
            human_text = human_plan.read_text(encoding="utf-8")
            for section in (
                "# 統合制作計画書",
                "## 2. 制作目的と採択内容",
                "## 3. 制作リファレンス",
                "https://example.com/references/concept",
                "https://example.com/references/visual-method",
                "## 7. 工程と作業手順",
                "## 9. 日程と予算",
                "## 11. 承認・安全境界",
                "## 13. 証跡と再現性",
                "PH001",
                "RQ001",
                "AT001",
                "AR001",
            ):
                self.assertIn(section, human_text)
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            self.assertEqual((project / "03_plan/production-plan.yaml").read_bytes(), first)
            self.assertEqual(human_plan.read_bytes(), first_human_plan)
            self.assertEqual(validate_project(project, ROOT), [])
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            self.assertEqual(plan["coverage_report"]["coverage_percent"], 100)
            self.assertEqual(plan["selection_record"]["status"], "HUMAN_SELECTED")
            self.assertEqual(plan["tasks"][-1]["status"], "READY")
            self.assertEqual(plan["approval_register"]["requirements"][0]["status"], "REQUIRED")
            self.assertEqual({item["access_status"] for item in plan["reference_access"]}, {"AVAILABLE"})

    def test_plan_is_derived_from_changed_handoff_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            hypotheses_path = project / "00_handoff/source-bundle/artifacts/production-hypotheses.yaml"
            hypotheses = load_yaml(hypotheses_path)
            hypotheses["hypotheses"][0]["title"] = "Changed input hypothesis"
            dump_yaml(hypotheses, hypotheses_path)

            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            self.assertEqual(plan["deliverables"][0]["title"], "Changed input hypothesis prototype")

    def test_uncovered_requirement_is_explicitly_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            requirements_path = project / "00_handoff/source-bundle/artifacts/production-requirements.yaml"
            requirements = load_yaml(requirements_path)
            requirements["requirements"][0]["acceptance_test_ids"] = []
            dump_yaml(requirements, requirements_path)

            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            self.assertEqual(plan["state"], "BLOCKED")
            self.assertEqual(plan["coverage_report"]["coverage_percent"], 0)
            self.assertEqual(plan["coverage_report"]["uncovered_requirement_ids"], ["RQ001"])
            self.assertTrue(any(gap["blocking"] and "RQ001" in gap["statement"] for gap in plan["gaps"]))

    def test_noncanonical_source_ref_index_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            source_refs_path = project / "00_handoff/source-bundle/artifacts/source-ref-index.yaml"
            source_refs = load_yaml(source_refs_path)
            source_refs["references"] = source_refs.pop("records")
            dump_yaml(source_refs, source_refs_path)

            self.assertEqual(build_plan_main(["--project-root", str(project), "--format", "json"]), 1)
            self.assertFalse((project / "03_plan/production-plan.yaml").exists())

    def test_integrated_human_plan_records_missing_reference_url_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            source_refs_path = project / "00_handoff/source-bundle/artifacts/source-ref-index.yaml"
            source_refs = load_yaml(source_refs_path)
            source_refs["records"][1].pop("access_url")
            dump_yaml(source_refs, source_refs_path)

            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            human_text = (project / "03_plan/production-plan.md").read_text(encoding="utf-8")
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            self.assertIn("URL未提供（gap参照）", human_text)
            self.assertIn("ビジュアル reference access URL is not supplied", human_text)
            self.assertIn("手法 reference access URL is not supplied", human_text)
            self.assertEqual(plan["reference_access"][1]["access_status"], "MISSING")
            reference_category_gaps = [gap for gap in plan["gaps"] if "reference access URL is not supplied" in gap["statement"]]
            self.assertEqual(len(reference_category_gaps), 2)
            self.assertTrue(all(gap["blocking"] for gap in reference_category_gaps))

    def test_integrated_human_plan_rejects_unsafe_reference_url_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            source_refs_path = project / "00_handoff/source-bundle/artifacts/source-ref-index.yaml"
            source_refs = load_yaml(source_refs_path)
            source_refs["records"][0]["access_url"] = "https://example.com/reference?token=secret"
            dump_yaml(source_refs, source_refs_path)

            self.assertEqual(build_plan_main(["--project-root", str(project), "--format", "json"]), 1)
            self.assertFalse((project / "03_plan/production-plan.md").exists())
            self.assertFalse((project / "03_plan/production-plan.yaml").exists())

    def test_plan_validator_rejects_unsafe_reference_url_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            plan_path = project / "03_plan/production-plan.yaml"
            plan = load_yaml(plan_path)
            plan["reference_access"][0]["access_url"] = "https://example.com/reference?token=secret"
            plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})}

            findings = validate_plan_document(plan, repository=ROOT, plan_path=plan_path)
            self.assertIn("PLANNING_REFERENCE_URL", {finding.rule for finding in findings})

    def test_integrated_human_plan_is_not_written_for_invalid_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            handoff_path = project / "00_handoff/production-handoff.yaml"
            handoff = load_yaml(handoff_path)
            handoff["selection"]["selected_hypothesis_id"] = "PH999"
            dump_yaml(handoff, handoff_path)

            self.assertEqual(build_plan_main(["--project-root", str(project), "--format", "json"]), 1)
            self.assertFalse((project / "03_plan/production-plan.md").exists())
            self.assertFalse((project / "03_plan/production-plan.yaml").exists())

    def test_legacy_human_brief_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            legacy_brief = project / "03_plan/human-brief.md"
            legacy_brief.write_text("# Human-maintained legacy brief\n", encoding="utf-8")

            self.assertEqual(build_plan_main(["--project-root", str(project), "--format", "json"]), 1)
            self.assertEqual(legacy_brief.read_text(encoding="utf-8"), "# Human-maintained legacy brief\n")

    def test_planning_cycle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            plan["tasks"][0]["depends_on"] = ["TK002", "TK004"]
            plan["tasks"][1]["depends_on"] = ["TK001"]
            findings = validate_plan_document(plan, repository=ROOT, plan_path=project / "03_plan/production-plan.yaml")
            self.assertIn("PLANNING_DAG_CYCLE", {finding.rule for finding in findings})

    def test_prototype_control_is_deterministic_and_does_not_execute_external_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            self.assertEqual(build_prototype_main(["--project-root", str(project)]), 0)
            first = (project / "04_prototype/prototype-control.yaml").read_bytes()
            self.assertEqual(build_prototype_main(["--project-root", str(project)]), 0)
            self.assertEqual((project / "04_prototype/prototype-control.yaml").read_bytes(), first)
            self.assertEqual(validate_project(project, ROOT), [])
            self.assertFalse((project / "04_prototype/prototype-control.yaml").read_text(encoding="utf-8").find("PHYSICAL_EXTERNAL") >= 0)

    def test_failed_prototype_requires_change_request_and_major_approval(self) -> None:
        control = self._prototype_control_fixture()
        control["runs"][0]["status"] = "FAILED"
        control["runs"][0]["external_validation_status"] = "VERIFIED"
        control["runs"][0]["evidence_refs"] = ["urn:production:evidence:PRT001"]
        control["test_results"][0].update({"result": "FAIL", "executed_at": "2026-08-12T12:00:00+09:00", "external_validation_status": "VERIFIED", "evidence_refs": ["urn:production:evidence:PTR001"]})
        control["reviews"][0].update({"status": "COMPLETE", "overall_result": "FAIL"})
        control["iteration_decisions"][0].update({"decision": "REVISE", "status": "APPROVAL_REQUIRED"})
        control["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in control.items() if key != "integrity"})}
        findings = validate_prototype_document(control, repository=ROOT, control_path=Path("prototype-control.yaml"), source_prototype_plan_ids={"PP001"})
        self.assertIn("PROTOTYPE_FAIL_NO_CHANGE", {finding.rule for finding in findings})

        control["change_requests"] = [{
            "id": "CR001", "trigger": "PROTOTYPE_TEST_FAILED", "requested_change": "Revise the prototype spacing after the failed frame review.",
            "affected_ids": ["TS001", "TK001"], "source_requirement_ids": ["RQ001"],
            "impact": {"classification": "MAJOR", "artistic": "May strengthen the interruption.", "technical": "Requires a new scale model.", "budget_delta": None, "schedule_delta_days": 1, "rights_safety": "NONE"},
            "alternatives": [{"id": "ALT001", "description": "Revise spacing and repeat the frame review.", "selected": False}],
            "research_review_required": True, "research_review_status": "PENDING", "approval_required": "HUMAN", "approval_status": "PENDING", "status": "PROPOSED",
            "baseline_ref": {"id": "SB001", "revision": 1, "content_sha256": "sha256:" + "1" * 64}, "trace_refs": ["HO001", "PH001", "RQ001", "CR001"],
        }]
        control["iteration_decisions"][0]["change_request_ids"] = ["CR001"]
        control["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in control.items() if key != "integrity"})}
        self.assertEqual(validate_prototype_document(control, repository=ROOT, control_path=Path("prototype-control.yaml"), source_prototype_plan_ids={"PP001"}), [])

    def test_prototype_pass_cannot_bypass_external_validation(self) -> None:
        control = self._prototype_control_fixture()
        control["test_results"][0].update({"result": "PASS", "executed_at": "2026-08-12T12:00:00+09:00"})
        control["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in control.items() if key != "integrity"})}
        findings = validate_prototype_document(control, repository=ROOT, control_path=Path("prototype-control.yaml"), source_prototype_plan_ids={"PP001"})
        self.assertIn("PROTOTYPE_PASS_EXTERNAL", {finding.rule for finding in findings})

    def test_runtime_bootstrap_replay_block_resume_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            runtime = Runtime(project, ROOT)
            first = runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            self.assertEqual(first["state"], "HANDOFF_VALIDATED")
            first = runtime.transition(to_state="PLANNING", occurred_at="2026-08-12T12:00:30+09:00", actor_kind="SYSTEM", actor_id="runtime/test", idempotency_key="transition/planning/1", reason="Synthetic planning initialization")
            self.assertEqual(first["state"], "PLANNING")
            blocked = runtime.transition(
                to_state="BLOCKED", occurred_at="2026-08-12T12:01:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test",
                idempotency_key="transition/block/1", reason="Synthetic approval blocker",
                payload={"blocker": "AR001", "impact": "prototype cannot start", "owner": "production", "resume_state": "PLANNING", "resolution_condition": "approval record exists"},
            )
            self.assertEqual(blocked["state"], "BLOCKED")
            resumed = runtime.transition(
                to_state="PLANNING", occurred_at="2026-08-12T12:02:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test",
                idempotency_key="transition/resume/1", reason="Synthetic blocker resolution",
                payload={"resume_state": "PLANNING", "resolution_evidence": ["urn:production:runtime:synthetic-resolution"]},
            )
            self.assertEqual(resumed["state"], "PLANNING")
            retry = runtime.transition(
                to_state="PLANNING", occurred_at="2026-08-12T12:02:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test",
                idempotency_key="transition/resume/1", reason="Synthetic blocker resolution",
                payload={"resume_state": "PLANNING", "resolution_evidence": ["urn:production:runtime:synthetic-resolution"]},
            )
            self.assertEqual(retry, resumed)
            self.assertEqual(len((project / "08_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines()), 3)
            self.assertEqual(runtime.replay(), resumed)
            self.assertEqual(validate_project(project, ROOT), [])

    def test_runtime_rejects_illegal_completion_and_tampered_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            runtime.transition(to_state="PLANNING", occurred_at="2026-08-12T12:00:30+09:00", actor_kind="SYSTEM", actor_id="runtime/test", idempotency_key="transition/planning/1", reason="Synthetic planning initialization")
            with self.assertRaises(DiagnosticError) as illegal:
                runtime.transition(to_state="COMPLETE", occurred_at="2026-08-12T12:01:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test", idempotency_key="transition/complete/1", reason="Not substantiated", payload={"completion_evidence": ["urn:production:runtime:fake"]})
            self.assertEqual(illegal.exception.finding.rule, "RUNTIME_ILLEGAL_TRANSITION")
            log_path = project / "08_runtime/run-log.jsonl"
            log_path.write_text(log_path.read_text(encoding="utf-8").replace("PROJECT_STATE_TRANSITIONED", "PROJECT_STATE_TAMPERED"), encoding="utf-8")
            with self.assertRaises(DiagnosticError) as tampered:
                runtime.replay()
            self.assertEqual(tampered.exception.finding.rule, "RUNTIME_EVENT_HASH")

    def test_runtime_rejects_state_projection_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            runtime.transition(to_state="PLANNING", occurred_at="2026-08-12T12:00:30+09:00", actor_kind="SYSTEM", actor_id="runtime/test", idempotency_key="transition/planning/1", reason="Synthetic planning initialization")
            state_path = project / "08_runtime/production-state.json"
            state_path.write_text(state_path.read_text(encoding="utf-8").replace('"state": "PLANNING"', '"state": "HANDOFF_VALIDATED"'), encoding="utf-8")
            with self.assertRaises(DiagnosticError) as divergence:
                runtime.replay()
            self.assertEqual(divergence.exception.finding.rule, "RUNTIME_STATE_DIVERGENCE")

    def test_runtime_does_not_repair_diverged_projection_during_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            runtime.transition(to_state="PLANNING", occurred_at="2026-08-12T12:00:30+09:00", actor_kind="SYSTEM", actor_id="runtime/test", idempotency_key="transition/planning/1", reason="Synthetic planning initialization")
            state_path = project / "08_runtime/production-state.json"
            original_state = state_path.read_text(encoding="utf-8")
            state_path.write_text(original_state.replace('"state": "PLANNING"', '"state": "HANDOFF_VALIDATED"'), encoding="utf-8")
            with self.assertRaises(DiagnosticError) as divergence:
                runtime.transition(
                    to_state="READY_FOR_PROTOTYPE",
                    occurred_at="2026-08-12T12:01:00+09:00",
                    actor_kind="SYSTEM",
                    actor_id="runtime/test",
                    idempotency_key="transition/ready/1",
                    reason="Should not repair a diverged projection",
                )
            self.assertEqual(divergence.exception.finding.rule, "RUNTIME_STATE_DIVERGENCE")
            self.assertEqual(original_state.replace('"state": "PLANNING"', '"state": "HANDOFF_VALIDATED"'), state_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len((project / "08_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines()))

    def test_runtime_rejects_partial_event_log_without_truncating_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            runtime.transition(to_state="PLANNING", occurred_at="2026-08-12T12:00:30+09:00", actor_kind="SYSTEM", actor_id="runtime/test", idempotency_key="transition/planning/1", reason="Synthetic planning initialization")
            log_path = project / "08_runtime/run-log.jsonl"
            original = log_path.read_bytes()
            log_path.write_bytes(original.rstrip(b"\n"))
            with self.assertRaises(DiagnosticError) as partial:
                runtime.replay()
            self.assertEqual(partial.exception.finding.rule, "RUNTIME_PARTIAL_LINE")
            self.assertEqual(original.rstrip(b"\n"), log_path.read_bytes())

    def test_runtime_task_lease_kill_resume_retry_and_effect_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._prepared_runtime_project(Path(directory))
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            runtime.initialize_task_graph(occurred_at="2026-08-12T12:00:01+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            self.assertEqual(runtime.next_task(occurred_at="2026-08-12T12:00:02+09:00"), "TK004")
            claimed = runtime.claim_task(
                task_id="TK004", occurred_at="2026-08-12T12:00:03+09:00", actor_id="worker/a",
                lease_token="lease-a", expires_at="2026-08-12T12:05:03+09:00", idempotency_key="claim/tk004/1",
            )
            self.assertEqual(claimed["task_states"]["TK004"]["attempt"], 1)
            self.assertEqual(
                runtime.claim_task(
                    task_id="TK004", occurred_at="2026-08-12T12:00:03+09:00", actor_id="worker/a",
                    lease_token="lease-a", expires_at="2026-08-12T12:05:03+09:00", idempotency_key="claim/tk004/1",
                ),
                claimed,
            )
            with self.assertRaises(DiagnosticError) as stale_heartbeat:
                runtime.heartbeat(
                    task_id="TK004", occurred_at="2026-08-12T12:00:04+09:00", actor_id="worker/b",
                    lease_token="lease-a", expires_at="2026-08-12T12:06:00+09:00", idempotency_key="heartbeat/tk004/bad",
                )
            self.assertEqual(stale_heartbeat.exception.finding.rule, "RUNTIME_LEASE_TOKEN")
            resumed = runtime.claim_task(
                task_id="TK004", occurred_at="2026-08-12T12:10:00+09:00", actor_id="worker/b",
                lease_token="lease-b", expires_at="2026-08-12T12:15:00+09:00", idempotency_key="claim/tk004/2",
            )
            self.assertEqual(resumed["task_states"]["TK004"]["attempt"], 2)
            runtime.retry_task(
                task_id="TK004", occurred_at="2026-08-12T12:11:00+09:00", actor_id="worker/b", lease_token="lease-b",
                retry_after="2026-08-12T12:12:00+09:00", reason="synthetic transient failure", idempotency_key="retry/tk004/1",
            )
            self.assertIsNone(runtime.next_task(occurred_at="2026-08-12T12:11:30+09:00"))
            runtime.claim_task(
                task_id="TK004", occurred_at="2026-08-12T12:12:01+09:00", actor_id="worker/c",
                lease_token="lease-c", expires_at="2026-08-12T12:17:00+09:00", idempotency_key="claim/tk004/3",
            )
            started = runtime.start_effect(
                task_id="TK004", occurred_at="2026-08-12T12:12:02+09:00", actor_id="worker/c", lease_token="lease-c",
                effect_key="effect/tk004/1", target_ref="urn:test:task:TK004", target_sha256="sha256:" + "1" * 64,
                idempotency_key="effect/tk004/1/start",
            )
            completed_effect = runtime.complete_effect(
                effect_key="effect/tk004/1", task_id="TK004", occurred_at="2026-08-12T12:12:03+09:00", actor_id="worker/c", lease_token="lease-c",
                status="SUCCEEDED", evidence_refs=["urn:test:evidence:TK004"], idempotency_key="effect/tk004/1/complete",
            )
            line_count = len((project / "08_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines())
            self.assertEqual(
                runtime.start_effect(
                    task_id="TK004", occurred_at="2026-08-12T12:12:04+09:00", actor_id="worker/c", lease_token="lease-c",
                    effect_key="effect/tk004/1", target_ref="urn:test:task:TK004", target_sha256="sha256:" + "1" * 64,
                    idempotency_key="effect/tk004/1/retry",
                ),
                completed_effect,
            )
            self.assertEqual(len((project / "08_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines()), line_count)
            final = runtime.complete_task(
                task_id="TK004", occurred_at="2026-08-12T12:12:05+09:00", actor_id="worker/c", lease_token="lease-c",
                evidence_refs=["urn:test:task-result:TK004"], idempotency_key="task/tk004/complete",
            )
            self.assertEqual(final["task_states"]["TK004"]["status"], "DONE")
            self.assertEqual(runtime.replay(), final)

    def test_runtime_rejects_expired_revoked_and_hash_mismatched_approvals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._prepared_runtime_project(Path(directory))
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            runtime.initialize_task_graph(occurred_at="2026-08-12T12:00:01+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            requirement = plan["approval_register"]["requirements"][0]
            approval = {
                "approval_id": "AP001", "revision": 1, "decision": "APPROVED",
                "scope": {"action": requirement["action"], "target_ref": requirement["target_ref"], "target_sha256": requirement["target_sha256"]},
                "approver": {"id": "human/test", "authority": "HUMAN"},
                "issued_at": "2026-08-12T12:00:02+09:00", "expires_at": "2026-08-12T12:05:00+09:00", "constraints": [],
            }
            runtime.record_approval(approval=approval, occurred_at="2026-08-12T12:00:02+09:00", actor_kind="HUMAN", actor_id="human/test", idempotency_key="approval/ap001/1")
            runtime.claim_task(
                task_id="TK001", occurred_at="2026-08-12T12:01:00+09:00", actor_id="worker/a",
                lease_token="lease-a", expires_at="2026-08-12T12:20:00+09:00", idempotency_key="claim/tk001/1",
            )
            with self.assertRaises(DiagnosticError) as expired:
                runtime.start_effect(
                    task_id="TK001", occurred_at="2026-08-12T12:06:00+09:00", actor_id="worker/a", lease_token="lease-a",
                    effect_key="effect/tk001/expired", target_ref=requirement["target_ref"], target_sha256=requirement["target_sha256"],
                    idempotency_key="effect/tk001/expired/start",
                )
            self.assertEqual(expired.exception.finding.rule, "RUNTIME_APPROVAL_EXPIRED")

            mismatched = dict(approval)
            mismatched["revision"] = 2
            mismatched["expires_at"] = "2026-08-12T13:00:00+09:00"
            mismatched["scope"] = dict(approval["scope"])
            mismatched["scope"]["target_sha256"] = "sha256:" + "3" * 64
            runtime.record_approval(approval=mismatched, occurred_at="2026-08-12T12:06:01+09:00", actor_kind="HUMAN", actor_id="human/test", idempotency_key="approval/ap001/2")
            with self.assertRaises(DiagnosticError) as hash_mismatch:
                runtime.start_effect(
                    task_id="TK001", occurred_at="2026-08-12T12:06:02+09:00", actor_id="worker/a", lease_token="lease-a",
                    effect_key="effect/tk001/hash", target_ref=requirement["target_ref"], target_sha256=requirement["target_sha256"],
                    idempotency_key="effect/tk001/hash/start",
                )
            self.assertEqual(hash_mismatch.exception.finding.rule, "RUNTIME_APPROVAL_HASH_MISMATCH")

            revoked = dict(approval)
            revoked["revision"] = 3
            revoked["expires_at"] = "2026-08-12T13:00:00+09:00"
            revoked["decision"] = "REVOKED"
            runtime.record_approval(approval=revoked, occurred_at="2026-08-12T12:06:03+09:00", actor_kind="HUMAN", actor_id="human/test", idempotency_key="approval/ap001/3")
            with self.assertRaises(DiagnosticError) as revoked_error:
                runtime.start_effect(
                    task_id="TK001", occurred_at="2026-08-12T12:06:04+09:00", actor_id="worker/a", lease_token="lease-a",
                    effect_key="effect/tk001/revoked", target_ref=requirement["target_ref"], target_sha256=requirement["target_sha256"],
                    idempotency_key="effect/tk001/revoked/start",
                )
            self.assertEqual(revoked_error.exception.finding.rule, "RUNTIME_APPROVAL_REVOKED")
            self.assertEqual(runtime.replay()["task_states"]["TK001"]["status"], "RUNNING")

    def test_runtime_unknown_effect_blocks_recovery_and_retry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._prepared_runtime_project(Path(directory))
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            runtime.initialize_task_graph(occurred_at="2026-08-12T12:00:01+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            runtime.claim_task(task_id="TK004", occurred_at="2026-08-12T12:00:02+09:00", actor_id="worker/a", lease_token="lease-a", expires_at="2026-08-12T12:05:00+09:00", idempotency_key="claim/tk004/unknown")
            runtime.start_effect(task_id="TK004", occurred_at="2026-08-12T12:00:03+09:00", actor_id="worker/a", lease_token="lease-a", effect_key="effect/tk004/unknown", target_ref="urn:test:unknown", target_sha256="sha256:" + "4" * 64, idempotency_key="effect/tk004/unknown/start")
            runtime.complete_effect(effect_key="effect/tk004/unknown", task_id="TK004", occurred_at="2026-08-12T12:00:04+09:00", actor_id="worker/a", lease_token="lease-a", status="UNKNOWN", evidence_refs=[], error_class="UNKNOWN_EXTERNAL", idempotency_key="effect/tk004/unknown/complete")
            with self.assertRaises(DiagnosticError) as recovery:
                runtime.recover_expired_lease(task_id="TK004", occurred_at="2026-08-12T12:06:00+09:00", idempotency_key="recover/tk004/unknown")
            self.assertEqual(recovery.exception.finding.rule, "RUNTIME_EXTERNAL_OUTCOME_UNKNOWN")
            with self.assertRaises(DiagnosticError) as retry:
                runtime.retry_task(task_id="TK004", occurred_at="2026-08-12T12:01:00+09:00", actor_id="worker/a", lease_token="lease-a", retry_after="2026-08-12T12:02:00+09:00", reason="must reconcile", idempotency_key="retry/tk004/unknown")
            self.assertEqual(retry.exception.finding.rule, "RUNTIME_EXTERNAL_OUTCOME_UNKNOWN")

    def test_runtime_rejects_wildcard_approval_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._prepared_runtime_project(Path(directory))
            runtime = Runtime(project, ROOT)
            runtime.bootstrap(occurred_at="2026-08-12T12:00:00+09:00", actor_kind="SYSTEM", actor_id="runtime/test")
            wildcard = {
                "approval_id": "AP009", "revision": 1, "decision": "APPROVED",
                "scope": {"action": "PHYSICAL_EXTERNAL", "target_ref": "urn:production:task:*", "target_sha256": "sha256:" + "9" * 64},
                "approver": {"id": "human/test", "authority": "HUMAN"},
                "issued_at": "2026-08-12T12:00:00+09:00", "expires_at": "2026-08-12T13:00:00+09:00", "constraints": [],
            }
            with self.assertRaises(DiagnosticError) as rejected:
                runtime.record_approval(approval=wildcard, occurred_at="2026-08-12T12:00:00+09:00", actor_kind="HUMAN", actor_id="human/test", idempotency_key="approval/ap009/1")
            self.assertEqual(rejected.exception.finding.rule, "RUNTIME_APPROVAL_WILDCARD")

    @staticmethod
    def _prepared_runtime_project(directory: Path) -> Path:
        output_root = directory / "output"
        if new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]) != 0:
            raise AssertionError("could not materialize runtime test project")
        project = output_root / "production/smoke"
        if build_plan_main(["--project-root", str(project)]) != 0:
            raise AssertionError("could not build runtime test plan")
        plan_path = project / "03_plan/production-plan.yaml"
        plan = load_yaml(plan_path)
        for resource in plan["resources"]:
            resource["availability"] = "AVAILABLE"
        for material in plan["materials"]:
            material["status"] = "APPROVED"
        plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})}
        dump_yaml(plan, plan_path)
        return project

    @staticmethod
    def _prototype_control_fixture() -> dict:
        return {
            "schema_version": "1.0.0", "control_id": "PC001", "control_revision": 1, "project_id": "production/minimal", "state": "PLANNING", "generated_at": "2026-08-12T12:00:00+09:00",
            "plan_ref": {"id": "PL001", "revision": 1, "content_sha256": "sha256:" + "1" * 64}, "source_prototype_plan_ids": ["PP001"],
            "runs": [{"id": "PRT001", "prototype_plan_id": "PP001", "production_task_ids": ["TK001"], "iteration": 1, "status": "BLOCKED", "external_validation_status": "REQUIRED", "evidence_refs": [], "test_result_ids": ["PTR001"], "review_id": "RV001", "started_at": None, "finished_at": None, "stop_reason": "Approval required.", "trace_refs": ["HO001", "PH001", "RQ001", "PP001"]}],
            "test_results": [{"id": "PTR001", "run_id": "PRT001", "acceptance_test_id": "AT001", "result": "NOT_RUN", "executed_at": None, "external_validation_status": "REQUIRED", "evidence_refs": [], "conditions": "Synthetic fixture.", "deviations": [], "limitations": "External execution is not present.", "trace_refs": ["HO001", "PH001", "RQ001", "PP001", "AT001"]}],
            "reviews": [{"id": "RV001", "run_id": "PRT001", "status": "NOT_STARTED", "assessments": [{"dimension": "TECHNICAL", "result": "NOT_REVIEWED", "rationale": "Pending evidence."}], "overall_result": "NOT_REVIEWED", "authority": "HUMAN", "human_required": True, "open_issue_ids": [], "external_validation_status": "REQUIRED", "trace_refs": ["HO001", "PH001", "RQ001", "PP001"]}],
            "iteration_decisions": [{"id": "ITD001", "run_id": "PRT001", "decision": "WAITING_FOR_RUN", "status": "RECORDED", "rationale": "Waiting for evidence.", "next_iteration": None, "change_request_ids": [], "authority": "SYSTEM", "trace_refs": ["HO001", "PH001", "RQ001", "PP001"]}],
            "change_requests": [], "integrity": {"content_sha256": "sha256:" + "0" * 64},
        }
