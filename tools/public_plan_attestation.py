"""Attest validated Production renderer bytes; never publish or transform a plan."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, unquote
import xml.etree.ElementTree as ET

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import build_plan
from tools.lib.canonical import canonical_json_bytes, canonical_sha256, sha256_bytes
from tools.lib.config import load_config
from tools.lib.schema import load_schema, validate_instance
from tools.lib.security import check_text_security, safe_relative_path
from tools.lib.yaml_io import load_yaml
from tools.validate import validate_project

ROOT = Path(__file__).resolve().parents[1]


def schema_check(value, name):
    path = ROOT / "schemas" / (name + ".schema.json")
    findings = validate_instance(value, load_schema(path), schema_path=path)
    if findings:
        raise ValueError("ATTESTATION_SCHEMA: " + findings[0].reason)


def public_text(text):
    policy = load_config(ROOT, "safety-policy.yaml")
    findings = check_text_security(text, file="public-plan", forbidden_markers=policy["forbidden_markers"], signed_url_markers=policy["signed_url_markers"])
    # A prose separator "sequence / encounter" is not an absolute path.
    # Keep the native marker/URL checks and require a path character after '/'.
    findings = [f for f in findings if f.rule != "PATH_IN_VALUE"]
    if re.search(r"(?:^|[\s(])(?:/[^\s|>]|[A-Za-z]:[\\/]|~[\\/]|file://)|\.\.[/\\]", text, re.I):
        raise ValueError("PUBLIC_PATH_BLOCKED")
    if findings or re.search(r"(?:-----BEGIN .*PRIVATE KEY|\b(?:ghp_|github_pat_|sk-)[A-Za-z0-9_-]{12,}|\bAuthorization\s*:|\b(?:password|api_key|access_token)\s*[:=])", text, re.I):
        raise ValueError("PUBLIC_CONTENT_BLOCKED: regenerate the canonical plan safely; do not trim its body")
    for url in re.findall(r"https?://[^\s<>\])\"']+", text):
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.hostname in {"localhost", "localhost.localdomain"}:
            raise ValueError("PUBLIC_URL_BLOCKED")
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            continue
        if not address.is_global:
            raise ValueError("PUBLIC_URL_BLOCKED")


def read_asset(project, relative, media_type):
    relative = safe_relative_path(relative)
    target = project / relative
    if target.resolve() != target or not target.is_file():
        raise ValueError("PUBLIC_ASSET_MISSING_OR_SYMLINK")
    raw = target.read_bytes()
    if media_type == "image/svg+xml":
        text = raw.decode("utf-8"); public_text(text)
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("PUBLIC_SVG_ACTIVE_CONTENT")
        xml = ET.fromstring(raw)
        if xml.tag.split("}")[-1] != "svg":
            raise ValueError("PUBLIC_ASSET_MIME")
        for node in xml.iter():
            if node.tag.split("}")[-1] in {"script", "foreignObject"} or any(k.lower().startswith("on") or (k.split("}")[-1] == "href" and not v.startswith("#")) or (k == "style" and "url(" in v.lower()) for k,v in node.attrib.items()):
                raise ValueError("PUBLIC_SVG_ACTIVE_CONTENT")
    elif media_type == "image/png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PUBLIC_ASSET_MIME")
    elif media_type == "image/jpeg" and not (raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")):
        raise ValueError("PUBLIC_ASSET_MIME")
    return raw


def linked_assets(body):
    result = set()
    for destination in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", body):
        if destination.startswith(("https://", "http://", "#")):
            continue
        if destination != unquote(destination) or any(c in destination for c in "?#<>"):
            raise ValueError("PUBLIC_ASSET_LINK")
        result.add("03_plan/" + safe_relative_path(destination))
    # Unsupported HTML embedding must not bypass the manifest.
    if re.search(r"<(?:img|iframe|object|embed|script)\b", body, re.I):
        raise ValueError("PUBLIC_UNMANIFESTED_EMBED")
    return result


def build_attestation(project_root, review, *, producer_commit, generated_at):
    project = Path(project_root).resolve()
    if project == ROOT or ROOT in project.parents:
        raise ValueError("explicit external project required")
    if not re.fullmatch(r"[0-9a-f]{40}", producer_commit) or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != producer_commit or subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("clean immutable producer checkout required")
    findings = validate_project(project, ROOT, check_attestation=False)
    if findings:
        raise ValueError("PRODUCTION_VALIDATION_FAILED: " + findings[0].rule)
    aggregate_path = project / "03_plan/production-plan.yaml"
    body_path = project / "03_plan/production-plan.md"
    if aggregate_path.resolve() != aggregate_path or body_path.resolve() != body_path:
        raise ValueError("canonical paths cannot be symlinks")
    aggregate_bytes, body = aggregate_path.read_bytes(), body_path.read_bytes()
    plan = load_yaml(aggregate_path)
    if build_plan._render_human_plan(project, plan).encode("utf-8") != body:
        raise ValueError("CANONICAL_RENDER_MISMATCH")
    public_text(body.decode("utf-8"))
    schema_check(review, "public-plan-review")
    policy = load_config(ROOT, "public-plan-policy.yaml")
    if review["policy_version"] != policy["version"] or review["aggregate_sha256"] != sha256_bytes(aggregate_bytes) or review["body_sha256"] != sha256_bytes(body):
        raise ValueError("PUBLIC_REVIEW_TARGET_MISMATCH")
    public_text(canonical_json_bytes(review).decode())
    assets = review["assets"]
    if len({a["path"] for a in assets}) != len(assets) or {a["path"] for a in assets} != linked_assets(body.decode("utf-8")):
        raise ValueError("PUBLIC_ASSET_MANIFEST_MISMATCH")
    for asset in assets:
        if not asset["path"].startswith("03_plan/media/"):
            raise ValueError("PUBLIC_ASSET_LAYOUT: regenerate through the current Production renderer")
        raw = read_asset(project, asset["path"], asset["media_type"])
        if sha256_bytes(raw) != asset["sha256"] or len(raw) != asset["byte_length"]:
            raise ValueError("PUBLIC_ASSET_HASH_MISMATCH")
    coverage = [{"domain": domain, "aggregate_field": field, "status": "VALIDATED"} for domain,field in policy["coverage"].items() if field in plan]
    if len(coverage) != len(policy["coverage"]):
        raise ValueError("PRODUCTION_COVERAGE_MISSING")
    attestation = {
        "contract_version": "production-public-plan-attestation/v1", "project_id": plan["project_id"],
        "plan_id": plan["plan_id"], "plan_revision": plan["plan_revision"], "generated_at": generated_at,
        "producer": {"repository": "masa-san-jp/agentic-art-production", "commit": producer_commit, "generator": "tools/public_plan_attestation.py", "renderer_contract_version": "production-plan-renderer/v1"},
        "aggregate": {"path": "03_plan/production-plan.yaml", "sha256": sha256_bytes(aggregate_bytes), "schema_version": plan["schema_version"]},
        "human_plan": {"path": "03_plan/production-plan.md", "sha256": sha256_bytes(body), "byte_length": len(body), "media_type": "text/markdown"},
        "body_transform": "none", "validator": {"name": "tools/validate.py", "version": "production-plan-validator/v1", "status": "PASSED"},
        "coverage": coverage, "assets": sorted(assets,key=lambda a:a["path"]), "publication_review": review,
        "external_effects_authorized": False,
    }
    attestation["integrity"] = {"canonicalization": "json-sort-keys-compact-utf8-v1", "content_sha256": canonical_sha256(attestation)}
    schema_check(attestation, "production-public-plan-attestation")
    return attestation


def verify_attestation(project, attestation):
    schema_check(attestation, "production-public-plan-attestation")
    if canonical_sha256({k:v for k,v in attestation.items() if k != "integrity"}) != attestation["integrity"]["content_sha256"]:
        raise ValueError("ATTESTATION_INTEGRITY")
    expected = build_attestation(project, attestation["publication_review"], producer_commit=attestation["producer"]["commit"], generated_at=attestation["generated_at"])
    if expected != attestation:
        raise ValueError("ATTESTATION_CONTENT_MISMATCH")


def write_attestation(path, attestation):
    data = canonical_json_bytes(attestation) + b"\n"
    if path.is_symlink(): raise ValueError("ATTESTATION_PATH_SYMLINK")
    if path.exists():
        if path.read_bytes() == data: return "ALREADY_ATTESTED"
        raise ValueError("ATTESTATION_CONFLICT")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        output.write(data); output.flush(); os.fsync(output.fileno()); temporary = Path(output.name)
    try:
        try: os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data: raise ValueError("ATTESTATION_CONFLICT")
            return "ALREADY_ATTESTED"
    finally: temporary.unlink()
    return "ATTESTED"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--producer-commit")
    parser.add_argument("--generated-at")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = args.project_root / "03_plan/public-plan-attestation.json"
        if args.check:
            verify_attestation(args.project_root, json.loads(path.read_text()))
            plan = load_yaml(args.project_root / "03_plan/production-plan.yaml")
            print(json.dumps({"status": "VERIFIED", "production_state": plan["state"]})); return 0
        else:
            if not args.review or not args.producer_commit or not args.generated_at: parser.error("generation requires --review, --producer-commit, --generated-at")
            result = write_attestation(path, build_attestation(args.project_root, json.loads(args.review.read_text()), producer_commit=args.producer_commit, generated_at=args.generated_at))
        print(json.dumps({"status": result})); return 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"status": "REJECTED", "reason": str(exc)})); return 1


if __name__ == "__main__": raise SystemExit(main())
