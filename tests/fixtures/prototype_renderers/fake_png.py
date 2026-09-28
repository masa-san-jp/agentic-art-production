#!/usr/bin/env python3
"""Write a tiny deterministic PNG for the renderer adapter contract tests."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path


def main() -> int:
    request = json.load(sys.stdin)
    relative_path = request["output_relative_path"]
    target = Path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A 2x2 opaque blue PNG. It is intentionally fixture-only and contains no
    # external image or model output.
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR42mP4z8DwH4QZGBgYGJgY"
        "GBgAAAwAAWgmWQ0AAAAASUVORK5CYII="
    )
    target.write_bytes(raw)
    print(json.dumps({
        "schema_version": "1.0.0",
        "output_relative_path": relative_path,
        "media_type": "image/png",
        "generator_id": "tests/fake-png-renderer",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
