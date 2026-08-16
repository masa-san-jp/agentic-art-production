#!/usr/bin/env python3
"""Receive a handoff bundle and materialize a new external production project."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.bundle import open_bundle
from tools.lib.canonical import canonical_sha256
from tools.lib.config import load_config
from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.production_brief import BRIEF_RELATIVE_PATH, load_production_brief
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.validate import validate_project


SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){1,62}[a-z0-9]$")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="lowercase production project slug")
    parser.add_argument("--handoff", required=True, type=Path, help="handoff directory or ZIP archive")
    parser.add_argument("--output-root", required=True, type=Path, help="Git-external output root")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _finding(rule: str, reason: str, *, file: Path | str = "", location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _assert_output_boundary(output_root: Path, repo_root: Path) -> None:
    output = output_root.resolve()
    repo = repo_root.resolve()
    if output == repo or repo in output.parents:
        raise DiagnosticError(
            _finding(
                "OUTPUT_ROOT_REPOSITORY",
                "output root is inside the protocol repository",
                file=output_root,
                remediation="Use a Git-external output root for materialized production projects.",
            )
        )


def _project_manifest(slug: str, handoff: dict, generated_at: str) -> dict:
    return {
        "schema_version": "1.0.0",
        "project_id": f"production/{slug}",
        "project_slug": slug,
        "workflow_mode": "PRODUCTION_HANDOFF",
        "state": "HANDOFF_VALIDATED",
        "created_at": generated_at,
        "handoff": {
            "handoff_id": handoff["handoff_id"],
            "revision": handoff["revision"],
            "content_sha256": handoff["integrity"]["content_sha256"],
            "receipt_status": "ACCEPTED",
        },
    }


def _receipt(handoff: dict, generated_at: str) -> dict:
    return {
        "receipt_id": "RC001",
        "receipt_status": "ACCEPTED",
        "handoff_id": handoff["handoff_id"],
        "revision": handoff["revision"],
        "handoff_sha256": handoff["integrity"]["content_sha256"],
        "schema_version": handoff["schema_version"],
        "received_at": generated_at,
        "rules": [],
    }


def _existing_project_is_same(target: Path, handoff: dict, repository: Path) -> bool:
    manifest_path = target / "manifest.yaml"
    if not manifest_path.is_file():
        return False
    try:
        manifest = load_yaml(manifest_path)
    except DiagnosticError:
        return False
    if not isinstance(manifest, dict):
        return False
    existing_handoff = manifest.get("handoff")
    expected_handoff = {
        "handoff_id": handoff.get("handoff_id"),
        "revision": handoff.get("revision"),
        "content_sha256": (handoff.get("integrity") or {}).get("content_sha256"),
        "receipt_status": "ACCEPTED",
    }
    if existing_handoff != expected_handoff:
        return False
    if manifest.get("project_slug") != target.name or manifest.get("project_id") != f"production/{target.name}":
        return False
    return not validate_project(target, repository)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _materialize(slug: str, output_root: Path, bundle, repository: Path) -> Path:
    layout = load_config(repository, "project-layout.yaml")
    production_root = output_root / str(layout["project_directory"])
    production_root.mkdir(parents=True, exist_ok=True)
    target = production_root / slug
    if target.exists() or target.is_symlink():
        if target.is_dir() and not target.is_symlink() and _existing_project_is_same(target, bundle.handoff, repository):
            return target
        raise DiagnosticError(_finding("PROJECT_IDEMPOTENCY_MISMATCH", "target project exists but does not match the accepted handoff", file=target, remediation="Use the original handoff for an idempotent retry or choose a new slug."))
    staging = Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=production_root))
    try:
        for directory in layout.get("directories", []):
            (staging / str(directory)).mkdir(parents=True, exist_ok=True)
        source_bundle = staging / "00_handoff/source-bundle"
        bundle.copy_to(source_bundle)
        shutil.copyfile(source_bundle / "manifest.yaml", staging / "00_handoff/source-bundle-manifest.yaml")
        shutil.copyfile(source_bundle / bundle.manifest["entrypoint"], staging / "00_handoff/production-handoff.yaml")
        generated_at = bundle.handoff.get("generated_at")
        if not isinstance(generated_at, str):
            generated_at = bundle.provenance.get("generated_at")
        if not isinstance(generated_at, str):
            raise DiagnosticError(_finding("HANDOFF_TIMESTAMP", "handoff or provenance must provide generated_at", file="production-handoff.yaml", remediation="Provide an injected RFC 3339 generated_at timestamp."))
        dump_yaml(_receipt(bundle.handoff, generated_at), staging / "00_handoff/handoff-receipt.yaml")
        dump_yaml(_project_manifest(slug, bundle.handoff, generated_at), staging / "manifest.yaml")
        state = {
            "schema_version": "1.0.0",
            "project_id": f"production/{slug}",
            "state": "HANDOFF_VALIDATED",
            "revision": 0,
            "last_event_id": None,
            "last_event_hash": None,
        }
        state["state_sha256"] = canonical_sha256(state)
        _write_json(staging / "08_runtime/production-state.json", state)
        (staging / "08_runtime/run-log.jsonl").write_text("", encoding="utf-8")
        _write_json(staging / "08_runtime/dependency-index.json", {"schema_version": "1.0.0", "nodes": [], "edges": []})
        _write_json(staging / "08_runtime/completion-report.json", {"status": "OPEN", "project_id": f"production/{slug}"})
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = repository_root()
    findings: list[Finding] = []
    try:
        if not SLUG_PATTERN.fullmatch(args.slug):
            raise DiagnosticError(_finding("PROJECT_SLUG", "slug must be 3-64 lowercase alphanumeric characters separated by single hyphens", file=args.slug, remediation="Use a slug such as harmony-production."))
        _assert_output_boundary(args.output_root, repo)
        with open_bundle(args.handoff, repo) as bundle:
            # The brief is a production-specific acceptance contract layered
            # on top of the upstream handoff schema.
            load_production_brief(bundle.root / BRIEF_RELATIVE_PATH, repository=repo)
            target = _materialize(args.slug, args.output_root.resolve(), bundle, repo)
        print(str(target))
    except DiagnosticError as exc:
        findings.append(exc.finding)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        findings.append(_finding("PROJECT_GENERATION", str(exc), remediation="Correct the bundle or output configuration and retry."))
    if findings:
        emit_findings(findings, output_format=args.format)
        return EXIT_VALIDATION
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
