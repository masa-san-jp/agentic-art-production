#!/usr/bin/env python3
"""Run the v1.0.0 release gate consecutively and optionally write external evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.release import run_release_gate
from tools.lib.yaml_io import dump_yaml


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=repository_root())
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--evidence", type=Path, help="Git-external YAML evidence path")
    parser.add_argument("--resume", action="store_true", help="Resume an IN_PROGRESS checkpoint at --evidence")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _write_evidence(path: Path, report: dict, repository: Path | None = None) -> None:
    path = path.resolve()
    repository = (repository or repository_root()).resolve()
    if path == repository or repository in path.parents:
        raise ValueError("release evidence must be written outside the protocol repository")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        dump_yaml(report, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        progress = (lambda message: print(message, flush=True)) if args.format == "text" else None
        report = run_release_gate(args.repository_root, runs=args.runs, checkpoint=args.evidence, resume=args.resume, progress=progress)
        if args.evidence:
            _write_evidence(args.evidence, report, args.repository_root)
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        else:
            print(f"RELEASE-001 {report['status']} candidate={report['candidate']} commit={report['verified_commit']}")
            for item in report["runs"]:
                print(f"run {item['run']}: {item['status']}")
            if args.evidence:
                print(f"evidence: {args.evidence.resolve()}")
        return 0 if report["status"] == "PASS" else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"RELEASE-001 FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
