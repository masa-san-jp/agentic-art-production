"""Deterministic serialization and hash helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


CANONICALIZATION_ID = "json-sort-keys-compact-utf8-v1"


def _ensure_json_value(value: Any, path: str = "/") -> None:
    """Reject values outside the canonical JSON scalar contract."""

    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        raise TypeError(f"canonical JSON does not accept float at {path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _ensure_json_value(item, f"{path.rstrip('/')}/{index}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical JSON object keys must be strings at {path}")
            _ensure_json_value(item, f"{path.rstrip('/')}/{key}")
        return
    raise TypeError(f"canonical JSON does not accept {type(value).__name__} at {path}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the v1 canonical JSON byte representation."""

    _ensure_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def handoff_hash_payload(handoff: dict[str, Any]) -> dict[str, Any]:
    """Remove the self-referential integrity block before hashing."""

    return {key: value for key, value in handoff.items() if key != "integrity"}


def handoff_sha256(handoff: dict[str, Any]) -> str:
    return canonical_sha256(handoff_hash_payload(handoff))


def result_hash_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Remove the self-referential integrity block before hashing a result."""

    return {key: value for key, value in result.items() if key != "integrity"}


def result_sha256(result: dict[str, Any]) -> str:
    return canonical_sha256(result_hash_payload(result))


def event_hash_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "event_sha256"}


def event_sha256(event: dict[str, Any]) -> str:
    return canonical_sha256(event_hash_payload(event))
