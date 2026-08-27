#!/usr/bin/env python3
"""Deterministic offline worker used by the harness contract tests."""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    invocation_id = str(request["invocation_id"])
    suffix = "".join(char for char in invocation_id if char.isdigit()) or "1"
    task = request["task_ref"]
    action = {
        "schema_version": "1.0.0",
        "action_id": f"AAC{int(suffix):06d}",
        "run_id": request["run_id"],
        "invocation_id": invocation_id,
        "context_sha256": request["context_sha256"],
        "capability_grant_sha256": request["capability_grant_sha256"],
        "task_ref": task,
        "lease_ref": {
            "task_id": task["id"],
            "owner": "scripted-worker",
            "expires_at": task["lease_expires_at"],
            "lease_token_sha256": task["lease_token_sha256"],
        },
        "kind": "RECORD_METADATA",
        "payload": {"proposal": "deterministic metadata-only worker proposal"},
        "idempotency_key": f"{invocation_id}/action/1",
        "decision": "PROPOSED",
    }
    json.dump({"actions": [action]}, sys.stdout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
