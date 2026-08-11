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
