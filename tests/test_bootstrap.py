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
            self.assertEqual(bundle.handoff["integrity"]["content_sha256"], "sha256:4fa114d37670d32cfe20b3fa53bd38b706357a6adf0b276d31e3ab467e565ab5")
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
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            self.assertEqual((project / "03_plan/production-plan.yaml").read_bytes(), first)
            self.assertEqual(validate_project(project, ROOT), [])
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            self.assertEqual(plan["coverage_report"]["coverage_percent"], 100)
            self.assertEqual(plan["selection_record"]["status"], "HUMAN_SELECTED")
            self.assertEqual(plan["tasks"][-1]["status"], "READY")
            self.assertEqual(plan["approval_register"]["requirements"][0]["status"], "REQUIRED")

    def test_planning_cycle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            self.assertEqual(new_production_main(["smoke", "--handoff", str(FIXTURE), "--output-root", str(output_root)]), 0)
            project = output_root / "production/smoke"
            self.assertEqual(build_plan_main(["--project-root", str(project)]), 0)
            plan = load_yaml(project / "03_plan/production-plan.yaml")
            plan["tasks"][2]["depends_on"] = ["TK002", "TK004"]
            plan["tasks"][3]["depends_on"] = ["TK003"]
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
