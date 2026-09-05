from __future__ import annotations
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import build_plan
from tools.new_production import main as new_production
from tools.public_plan_attestation import build_attestation, verify_attestation, write_attestation, linked_assets, main
from tools.lib.canonical import sha256_bytes, canonical_sha256
from tools.lib.yaml_io import load_yaml, dump_yaml
from tools.validate import validate_project

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-05T00:00:00Z"


class PublicPlanAttestationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.assertEqual(0, new_production(["attestation", "--handoff", str(ROOT / "tests/fixtures/handoff/minimal"), "--output-root", str(self.output)]))
        self.project = self.output / "production/attestation"
        self.assertEqual(0, build_plan.main(["--project-root", str(self.project)]))
        self.code = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    def review(self):
        body = (self.project / "03_plan/production-plan.md").read_bytes()
        return {"contract_version": "public-plan-review/v1", "policy_version": "public-plan-policy/v1",
            "aggregate_sha256": sha256_bytes((self.project / "03_plan/production-plan.yaml").read_bytes()),
            "body_sha256": sha256_bytes(body), "content_safety": "PASSED", "rights": "PASSED", "consent": "PASSED",
            "consent_ref": "consent/synthetic-fixture-only", "assets": [
                {"path": path, "sha256": sha256_bytes((self.project/path).read_bytes()), "byte_length": (self.project/path).stat().st_size,
                 "media_type": "image/svg+xml", "rights_status": "PUBLIC_CLEARED", "rights_ref": "rights/synthetic-fixture-only"}
                for path in sorted(linked_assets(body.decode()))]}

    def attest(self, review=None):
        return build_attestation(self.project, review or self.review(), producer_commit=self.code, generated_at=NOW)

    def test_canonical_schema_integrity_assets_determinism_and_cli_replay(self):
        first = self.attest(); self.assertEqual(first, self.attest()); verify_attestation(self.project, first)
        self.assertEqual(18, len(first["coverage"])); self.assertFalse(first["external_effects_authorized"])
        path = self.project / "03_plan/public-plan-attestation.json"
        self.assertEqual("ATTESTED", write_attestation(path, first)); self.assertEqual("ALREADY_ATTESTED", write_attestation(path, first))
        self.assertEqual(0, main(["--project-root", str(self.project), "--check"]))
        self.assertEqual([], validate_project(self.project, ROOT))
        changed = copy.deepcopy(first); changed["plan_revision"] += 1
        with self.assertRaises(ValueError): write_attestation(path, changed)
        with self.assertRaisesRegex(ValueError, "INTEGRITY"): verify_attestation(self.project, changed)
        path.write_text(json.dumps(changed))
        self.assertIn("PUBLIC_PLAN_ATTESTATION", {finding.rule for finding in validate_project(self.project, ROOT)})

    def test_body_one_byte_summary_and_newline_normalization_fail(self):
        path = self.project / "03_plan/production-plan.md"; original = path.read_bytes(); review = self.review()
        for bad in (original + b" ", b"# Summary\n", original.replace(b"\n", b"\r\n")):
            path.write_bytes(bad)
            with self.assertRaises(ValueError): self.attest(review)
        path.write_bytes(original)

    def test_revision_asset_missing_tamper_and_unknown_rights_fail(self):
        original = self.attest()
        altered = copy.deepcopy(original); altered["plan_revision"] += 1
        altered["integrity"]["content_sha256"] = canonical_sha256({k:v for k,v in altered.items() if k != "integrity"})
        with self.assertRaisesRegex(ValueError, "CONTENT_MISMATCH"): verify_attestation(self.project, altered)
        for field in ("rights", "consent", "content_safety"):
            review = self.review(); review[field] = "UNKNOWN"
            with self.assertRaises(ValueError): self.attest(review)
        asset = self.project / original["assets"][0]["path"]; raw = asset.read_bytes()
        asset.write_bytes(raw + b"tamper")
        with self.assertRaises(ValueError): self.attest(original["publication_review"])
        asset.unlink()
        with self.assertRaises(ValueError): self.attest(original["publication_review"])

    def test_renderer_heading_change_does_not_create_receiver_heading_contract(self):
        render = build_plan._render_human_plan
        def changed(project, plan):
            return render(project,plan).replace("# ", "# Revised wording: ", 1)
        with patch.object(build_plan, "_render_human_plan", changed):
            self.assertEqual(0, build_plan.main(["--project-root", str(self.project)]))
            value = self.attest(); verify_attestation(self.project, value)
            self.assertEqual("none", value["body_transform"])

    def test_stale_review_and_unlisted_asset_rejected(self):
        review = self.review(); review["body_sha256"] = "sha256:" + "0"*64
        with self.assertRaisesRegex(ValueError, "TARGET_MISMATCH"): self.attest(review)
        review = self.review(); review["assets"] = []
        with self.assertRaisesRegex(ValueError, "MANIFEST_MISMATCH"): self.attest(review)
