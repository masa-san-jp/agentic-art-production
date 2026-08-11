"""Path, text, URI, and bundle safety checks."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit

from .diagnostics import DiagnosticError, Finding


ABSOLUTE_PATH = re.compile(r"(?:^|[\s(])(?:/|[A-Za-z]:[\\/]|~[\\/]|file://)", re.IGNORECASE)


def safe_relative_path(value: str, *, max_bytes: int = 240, max_depth: int = 8) -> str:
    if not value or "\x00" in value:
        raise DiagnosticError(Finding("PATH_INVALID", "path is empty or contains NUL", location=value, remediation="Use a non-empty POSIX relative path."))
    if "\\" in value or value.startswith("/"):
        raise DiagnosticError(Finding("PATH_INVALID", "backslash and absolute paths are not allowed", location=value, remediation="Use a POSIX relative path inside the bundle."))
    path = PurePosixPath(value)
    parts = path.parts
    if path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise DiagnosticError(Finding("PATH_TRAVERSAL", "path contains an unsafe segment", location=value, remediation="Remove absolute and traversal segments."))
    if len(value.encode("utf-8")) > max_bytes:
        raise DiagnosticError(Finding("PATH_TOO_LONG", "path exceeds the configured byte limit", location=value, remediation="Shorten the relative path."))
    if len(parts) > max_depth:
        raise DiagnosticError(Finding("PATH_TOO_DEEP", "path exceeds the configured depth limit", location=value, remediation="Reduce directory nesting."))
    return "/".join(parts)


def iter_string_values(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield prefix or "/", value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from iter_string_values(child, f"{prefix}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_string_values(child, f"{prefix}/{index}")


def check_text_security(value: Any, *, file: str, forbidden_markers: Iterable[str], signed_url_markers: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    markers = [marker.upper() for marker in forbidden_markers]
    signed = [marker.lower() for marker in signed_url_markers]
    for location, text in iter_string_values(value):
        upper = text.upper()
        if any(marker in upper for marker in markers):
            findings.append(Finding("PRIVATE_MARKER", "value contains a Git-prohibited classification marker", file=file, location=location, remediation="Remove private/raw material and retain only approved derived metadata."))
        if ABSOLUTE_PATH.search(text) or "../" in text or "..\\" in text:
            findings.append(Finding("PATH_IN_VALUE", "value contains an absolute or traversal path", file=file, location=location, remediation="Use a project-relative reference or approved opaque URI."))
        lowered = text.lower()
        if ("https://" in lowered or "http://" in lowered) and any(marker in lowered for marker in signed):
            findings.append(Finding("SIGNED_URL", "value contains a signed or credential-bearing URL", file=file, location=location, remediation="Use a stable URI and content hash without credentials or signed query parameters."))
    return findings


def validate_asset_uri(uri: str, *, allowed_schemes: Iterable[str], allow_query: bool = False) -> Finding | None:
    parsed = urlsplit(uri)
    scheme = parsed.scheme.lower()
    if scheme not in {item.lower() for item in allowed_schemes}:
        return Finding("ASSET_URI_SCHEME", f"URI scheme {scheme!r} is not allowed", remediation="Use an approved opaque asset URI scheme.")
    if parsed.username or parsed.password:
        return Finding("ASSET_URI_CREDENTIAL", "asset URI contains userinfo", remediation="Remove credentials from the URI.")
    if parsed.fragment:
        return Finding("ASSET_URI_FRAGMENT", "asset URI contains a fragment", remediation="Remove the fragment and use a stable versioned URI.")
    if parsed.query and not allow_query:
        return Finding("ASSET_URI_QUERY", "asset URI contains a query string", remediation="Remove signed or mutable query parameters.")
    return None
