#!/usr/bin/env python3
"""Run the deterministic offline EVAL-001 acceptance matrix."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.evaluation import render_evaluation, run_evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_evaluation()
    print(render_evaluation(report, args.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
