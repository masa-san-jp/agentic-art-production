"""Safe YAML/JSON/JSONL loading with duplicate-key diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .diagnostics import DiagnosticError, Finding


class UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DiagnosticError(
                Finding(
                    rule="YAML_DUPLICATE_KEY",
                    file="<yaml>",
                    location=str(key),
                    reason=f"duplicate YAML key {key!r}",
                    remediation="Remove the duplicate key and keep one canonical value.",
                )
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def read_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DiagnosticError(
            Finding("FILE_READ", str(exc), file=str(path), remediation="Restore the file or correct the path.")
        ) from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise DiagnosticError(
            Finding("UTF8_BOM", "UTF-8 BOM is not allowed", file=str(path), remediation="Save the file as UTF-8 without a BOM.")
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiagnosticError(
            Finding("INVALID_UTF8", str(exc), file=str(path), remediation="Encode the file as UTF-8 text.")
        ) from exc


def load_yaml(path: Path) -> Any:
    text = read_text(path)
    try:
        value = yaml.load(text, Loader=UniqueSafeLoader)
    except DiagnosticError as exc:
        finding = exc.finding
        raise DiagnosticError(
            Finding(
                finding.rule,
                finding.reason,
                file=str(path),
                location=finding.location,
                line=finding.line,
                remediation=finding.remediation,
                context=finding.context,
            )
        ) from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise DiagnosticError(
            Finding(
                "YAML_SYNTAX",
                str(exc),
                file=str(path),
                line=(mark.line + 1 if mark else None),
                remediation="Fix YAML syntax and validate again.",
            )
        ) from exc
    return value


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    text = read_text(path)
    try:
        return json.loads(text, object_pairs_hook=_unique_json_pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (json.JSONDecodeError, ValueError) as exc:
        raise DiagnosticError(
            Finding("JSON_SYNTAX", str(exc), file=str(path), remediation="Fix JSON syntax or duplicate keys and validate again.")
        ) from exc


def load_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    text = read_text(path)
    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise DiagnosticError(
                Finding("JSONL_BLANK_LINE", "blank JSONL lines are not allowed", file=str(path), line=line_number, remediation="Remove the blank line or add a JSON object.")
            )
        try:
            value = json.loads(line, object_pairs_hook=_unique_json_pairs)
        except (json.JSONDecodeError, ValueError) as exc:
            raise DiagnosticError(
                Finding("JSONL_SYNTAX", str(exc), file=str(path), line=line_number, remediation="Make each line one valid JSON object with unique keys.")
            ) from exc
        if not isinstance(value, dict):
            raise DiagnosticError(
                Finding("JSONL_OBJECT_REQUIRED", "each JSONL line must be an object", file=str(path), line=line_number, remediation="Replace the line with a JSON object.")
            )
        records.append((line_number, value))
    return records


def dump_yaml(value: Any, path: Path) -> None:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
