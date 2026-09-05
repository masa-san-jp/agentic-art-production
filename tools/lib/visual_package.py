"""Deterministic, viewable visual package generation and validation."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .canonical import canonical_sha256, sha256_bytes
from .diagnostics import Finding
from .schema import load_schema, validate_instance
from .security import safe_relative_path


VISUAL_PACKAGE_SCHEMA = "schemas/visual-package.schema.json"
PACKAGE_RELATIVE_PATH = "03_plan/visual-package.yaml"
BOARD_RELATIVE_PATH = "03_plan/media/visual-reference-board.svg"
MOCKUP_RELATIVE_PATH = "03_plan/media/concept-mockup.svg"
BAD_RIGHTS = {"UNKNOWN", "REVIEW_REQUIRED"}


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _text(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return fallback


def _strings(value: Any, fallback: str) -> list[str]:
    if isinstance(value, list):
        result = [_text(item, "") for item in value]
        result = [item for item in result if item]
        if result:
            return list(dict.fromkeys(result))
    return [fallback]


def _xml(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _visual_task_id(plan: dict[str, Any]) -> str:
    tasks = plan.get("tasks", [])
    for task in tasks:
        if isinstance(task, dict) and task.get("effect_type") == "READ_ONLY" and isinstance(task.get("id"), str):
            return task["id"]
    for task in tasks:
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            return task["id"]
    return "TK001"


def _palette(visual_language: dict[str, Any] | None) -> list[dict[str, str]]:
    preferred = ((visual_language or {}).get("palette") or {}).get("preferred", [])
    if isinstance(preferred, list) and preferred:
        # Research owns the visual-language words. They are shown as labels;
        # the SVG itself remains a neutral deterministic fixture rendering.
        return [
            {"name": _text(value, f"preferred-{index}"), "hex": color, "role": "research visual-language label"}
            for index, (value, color) in enumerate(zip(preferred[:4], ["#111827", "#374151", "#9ca3af", "#f3f4f6"]), start=1)
        ]
    return [
        {"name": "ink", "hex": "#111827", "role": "neutral fixture foreground"},
        {"name": "slate", "hex": "#475569", "role": "neutral fixture structure"},
        {"name": "mist", "hex": "#cbd5e1", "role": "neutral fixture interval"},
        {"name": "paper", "hex": "#f8fafc", "role": "neutral fixture ground"},
    ]


def _svg_text(lines: list[tuple[int, int, int, str, str]]) -> str:
    return "\n".join(
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-family="sans-serif">{_xml(value)}</text>'
        for x, y, size, value, color in lines
    )


def _board_svg(board: dict[str, Any], package_id: str) -> bytes:
    palette = board["palette"]
    # Research may intentionally provide a single preferred palette label.
    # Keep that source-owned vocabulary unchanged and reuse its deterministic
    # foreground color for secondary board text instead of requiring a second
    # invented color.
    secondary = palette[1] if len(palette) > 1 else palette[0]
    refs = board["references"]
    lines = [
        (48, 64, 30, board["title"], palette[0]["hex"]),
        (48, 98, 15, f"{package_id} · REFERENCE BOARD · CITATION ONLY", secondary["hex"]),
        (48, 142, 18, "No external image body is copied into this fixture.", secondary["hex"]),
    ]
    for index, ref in enumerate(refs[:5]):
        y = 196 + index * 78
        lines.extend([
            (78, y, 18, f"{ref['source_ref_id']} · {ref['rights_status']}", palette[0]["hex"]),
            (78, y + 26, 14, ref["derivation_evidence"], secondary["hex"]),
            (78, y + 49, 12, ref["locator"], secondary["hex"]),
        ])
    swatch_x = 780
    for index, item in enumerate(palette):
        x = swatch_x + (index % 2) * 170
        y = 190 + (index // 2) * 100
        lines.append((x, y + 82, 13, f"{item['name']} · {item['role']}", palette[0]["hex"]))
    lines.extend([
        (780, 430, 16, "MATERIAL", palette[0]["hex"]),
        (780, 458, 13, "; ".join(board["materials"]), secondary["hex"]),
        (780, 514, 16, "LIGHT / SPACE", palette[0]["hex"]),
        (780, 542, 13, "; ".join(board["light"] + board["space"]), secondary["hex"]),
        (48, 690, 13, board["rendering_note"], secondary["hex"]),
    ])
    swatches = "\n".join(
        f'<rect x="{swatch_x + (index % 2) * 170}" y="{170 + (index // 2) * 100}" width="150" height="60" rx="8" fill="{item["hex"]}" stroke="#64748b" />'
        for index, item in enumerate(palette)
    )
    cards = "\n".join(
        f'<rect x="48" y="{168 + index * 78}" width="650" height="62" rx="8" fill="#ffffff" stroke="#cbd5e1" />'
        for index, _ref in enumerate(refs[:5])
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760">
<title>{_xml(board["title"])}</title>
<desc>Deterministic synthetic visual reference board. Citation-only references; no external image body.</desc>
<rect width="1200" height="760" fill="#f8fafc" />
{cards}
{swatches}
{_svg_text(lines)}
</svg>
'''
    return svg.encode("utf-8")


def _mockup_svg(mockup: dict[str, Any], package_id: str) -> bytes:
    foreground = "#111827"
    muted = "#475569"
    bars = "\n".join(
        f'<rect x="{260 + index * 82}" y="220" width="34" height="270" fill="#475569" opacity="{0.35 + index * 0.08:.2f}" />'
        for index in range(7)
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760">
<title>{_xml(mockup["title"])}</title>
<desc>Conceptual simulated mockup only. Physical and external validation have not been run.</desc>
<rect width="1200" height="760" fill="#f8fafc" />
<line x1="130" y1="540" x2="1040" y2="540" stroke="#94a3b8" stroke-width="4" />
{bars}
<circle cx="160" cy="525" r="18" fill="#f59e0b" />
<line x1="160" y1="525" x2="260" y2="400" stroke="#f59e0b" stroke-width="3" stroke-dasharray="8 8" />
<text x="48" y="64" font-size="30" fill="{foreground}" font-family="sans-serif">{_xml(mockup["title"])}</text>
<text x="48" y="98" font-size="15" fill="{muted}" font-family="sans-serif">{_xml(package_id)} · {mockup["representation"]} · NOT TO SCALE</text>
<text x="48" y="620" font-size="16" fill="{foreground}" font-family="sans-serif">Viewer position → repeated elements → interrupted sight line</text>
<text x="48" y="662" font-size="13" fill="{muted}" font-family="sans-serif">{_xml(mockup["physical_validation_note"])}</text>
<text x="48" y="700" font-size="13" fill="{muted}" font-family="sans-serif">{_xml(mockup["rendering_note"])}</text>
</svg>
'''
    return svg.encode("utf-8")


def visual_asset_bytes(package: dict[str, Any]) -> dict[str, bytes]:
    """Render package assets from package metadata without external effects."""
    return {
        package["board"]["relative_path"]: _board_svg(package["board"], package["package_id"]),
        package["mockup"]["relative_path"]: _mockup_svg(package["mockup"], package["package_id"]),
    }


def build_visual_package(
    plan: dict[str, Any],
    *,
    handoff: dict[str, Any],
    brief: dict[str, Any],
    reference_access: list[dict[str, Any]],
    visual_language: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build package metadata and deterministic SVG bytes represented by it."""
    source_refs = []
    for item in reference_access:
        source_id = str(item["source_ref_id"])
        locator = item.get("access_url") or f"urn:source-ref:{source_id}"
        source_refs.append({
            "source_ref_id": source_id,
            "locator": locator,
            "derivation_evidence": _text(item.get("summary"), f"Derived from accepted handoff reference {source_id}."),
            "rights_status": "UNKNOWN",
            "adoption_status": "CITATION_ONLY",
            "rejection_reason": "No external asset body is adopted; rights confirmation remains human-gated.",
        })
    source_refs = sorted(source_refs, key=lambda item: item["source_ref_id"])
    if not source_refs:
        # The handoff contract normally prevents this. Keeping the explicit
        # gap here makes a malformed future handoff visible instead of making
        # an empty board look complete.
        source_refs = [{
            "source_ref_id": "MISSING",
            "locator": "urn:source-ref:missing",
            "derivation_evidence": "No source reference was supplied by the accepted handoff.",
            "rights_status": "UNKNOWN",
            "adoption_status": "REJECTED",
            "rejection_reason": "Visual package cannot adopt an unreferenced source.",
        }]
    source_repository = _text(handoff.get("research_project_id"), "accepted-handoff")
    source_commit = _text(handoff.get("research_commit"), "0" * 40)
    source_at_commit = f"{source_repository}@{source_commit}"
    evidence_locator = f"handoff:{handoff.get('handoff_id', 'unknown')}#source_refs/{source_refs[0]['source_ref_id']}"
    generated_by_task_id = _visual_task_id(plan)
    palette = _palette(visual_language)
    completion = brief.get("completion_image") if isinstance(brief.get("completion_image"), dict) else {}
    concept = brief.get("concept") if isinstance(brief.get("concept"), dict) else {}
    theme = brief.get("theme") if isinstance(brief.get("theme"), dict) else {}
    title = _text(plan.get("selection_record", {}).get("selected_hypothesis_id"), "Accepted production concept")
    title = _text((plan.get("selection_record") or {}).get("rationale"), title)
    title = title[:120]
    board_refs = [
        {
            "source_ref_id": item["source_ref_id"],
            "locator": item["locator"],
            "derivation_evidence": item["derivation_evidence"],
            "rights_status": item["rights_status"],
        }
        for item in source_refs
    ]
    provenance = {
        "source_repository": source_repository,
        "source_commit": source_commit,
        "source_repository_at_commit": source_at_commit,
        "evidence_locator": evidence_locator,
        "generated_by_task_id": generated_by_task_id,
    }
    board = {
        "kind": "BOARD",
        "title": f"Visual reference board · {title}",
        "relative_path": BOARD_RELATIVE_PATH,
        "asset_ref": {
            "asset_id": "AS001",
            "uri": "urn:asset:visual-package:VP001-board",
            "version": "1.0.0",
            "sha256": "sha256:" + "0" * 64,
            "media_type": "image/svg+xml",
            "rights_status": "PROJECT_INTERNAL",
        },
        "provenance": provenance,
        "safety": {
            "rights_status": "PROJECT_INTERNAL",
            "safety_status": "CLEAR",
            "external_validation_status": "NOT_RUN",
            "physical_validation_status": "NOT_RUN",
            "safety_note": "Synthetic fixture board only; no external material or physical work was used.",
        },
        "source_ref_ids": [item["source_ref_id"] for item in source_refs],
        "references": board_refs,
        "palette": palette,
        "materials": _strings(concept.get("materials"), "Material detail is not supplied by the accepted handoff."),
        "light": _strings(concept.get("light"), "Light detail is not supplied by the accepted handoff."),
        "space": _strings([completion.get("position"), theme.get("field")], "Spatial detail is not supplied by the accepted handoff."),
        "rendering_note": "Neutral deterministic rendering of accepted references; this is not an external image acquisition.",
    }
    mockup = {
        "kind": "MOCKUP",
        "title": f"Concept mockup · {title}",
        "relative_path": MOCKUP_RELATIVE_PATH,
        "asset_ref": {
            "asset_id": "AS002",
            "uri": "urn:asset:visual-package:VP001-mockup",
            "version": "1.0.0",
            "sha256": "sha256:" + "0" * 64,
            "media_type": "image/svg+xml",
            "rights_status": "PROJECT_INTERNAL",
        },
        "provenance": provenance,
        "safety": {
            "rights_status": "PROJECT_INTERNAL",
            "safety_status": "REVIEW_REQUIRED",
            "external_validation_status": "NOT_RUN",
            "physical_validation_status": "NOT_RUN",
            "safety_note": "Conceptual/simulated only; dimensions, structural safety, venue safety, and external validation remain human-gated.",
        },
        "representation": "CONCEPTUAL",
        "dimensions": "Not to scale; physical dimensions are not supplied by the accepted handoff.",
        "materials": "Material selection remains provisional and is not a purchase or fabrication instruction.",
        "placement": _text(completion.get("position"), "Placement is not supplied by the accepted handoff."),
        "physical_validation_note": "PHYSICAL_EXTERNAL validation: NOT_RUN. No physical prototype, publication, purchase, contract, or Drive share was performed.",
        "rendering_note": "Synthetic conceptual diagram generated from accepted brief fields; not a rendered physical work.",
    }
    package = {
        "schema_version": "1.0.0",
        "package_id": "VP001",
        "revision": 1,
        "status": "READY" if reference_access else "BLOCKED",
        "board": board,
        "mockup": mockup,
        "source_refs": source_refs,
        "gaps": [] if reference_access else [{"id": "VG001", "statement": "Accepted handoff contains no source reference for the visual board.", "blocking": True}],
        "determinism": {
            "algorithm": "visual-package-v1",
            "source_input_sha256": plan["determinism"]["source_input_sha256"],
            "seed": plan["determinism"]["source_input_sha256"],
        },
    }
    assets = visual_asset_bytes(package)
    package["board"]["asset_ref"]["sha256"] = sha256_bytes(assets[BOARD_RELATIVE_PATH])
    package["mockup"]["asset_ref"]["sha256"] = sha256_bytes(assets[MOCKUP_RELATIVE_PATH])
    package["integrity"] = {"content_sha256": canonical_sha256(package)}
    return package


def validate_visual_package(
    package: Any,
    *,
    repository: Path,
    project_root: Path,
    plan_path: Path | str,
    plan: dict[str, Any] | None = None,
    require_files: bool = True,
) -> list[Finding]:
    """Validate schema, package integrity, file links, hashes, and safety."""
    package_path = project_root / PACKAGE_RELATIVE_PATH
    schema_path = repository / VISUAL_PACKAGE_SCHEMA
    findings: list[Finding] = []
    if not isinstance(package, dict):
        return [_finding("VISUAL_PACKAGE_OBJECT", "visual_package must be an object", file=plan_path, remediation="Regenerate the production plan with a visual package.")]
    try:
        schema = load_schema(schema_path)
        common = load_schema(repository / "schemas/common.schema.json")
        asset = load_schema(repository / "schemas/asset-reference.schema.json")
        findings.extend(validate_instance(package, schema, schema_path=schema_path, common_schema=common, schema_store=[asset]))
    except Exception as exc:
        return [_finding("VISUAL_PACKAGE_SCHEMA", str(exc), file=schema_path, remediation="Restore the visual package schema and retry validation.")]
    if findings:
        return findings
    expected_integrity = canonical_sha256({key: value for key, value in package.items() if key != "integrity"})
    if package["integrity"]["content_sha256"] != expected_integrity:
        findings.append(_finding("VISUAL_PACKAGE_INTEGRITY", "visual package content hash does not match its canonical payload", file=plan_path, location="/visual_package/integrity/content_sha256", remediation="Regenerate the package and preserve its canonical integrity hash."))
    task_ids = {task.get("id") for task in (plan or {}).get("tasks", []) if isinstance(task, dict)}
    for name in ("board", "mockup"):
        asset_record = package[name]
        location = f"/visual_package/{name}"
        if asset_record["asset_ref"]["rights_status"] in BAD_RIGHTS:
            findings.append(_finding("VISUAL_PACKAGE_RIGHTS", f"{name} asset has unapproved rights status {asset_record['asset_ref']['rights_status']}", file=plan_path, location=f"{location}/asset_ref/rights_status", remediation="Use a generated project-internal asset or resolve rights through the human gate."))
        if asset_record["safety"]["safety_status"] == "UNKNOWN":
            findings.append(_finding("VISUAL_PACKAGE_SAFETY", f"{name} asset has unknown safety status", file=plan_path, location=f"{location}/safety/safety_status", remediation="Record a known safety status; do not treat unknown safety as complete."))
        generated_task = asset_record["provenance"]["generated_by_task_id"]
        if plan is not None and generated_task not in task_ids:
            findings.append(_finding("VISUAL_PACKAGE_TASK_REFERENCE", f"{name} asset references unknown generated task {generated_task}", file=plan_path, location=f"{location}/provenance/generated_by_task_id", remediation="Reference a task from the same validated production plan."))
        relative = asset_record["relative_path"]
        try:
            safe_relative_path(relative)
        except Exception:
            findings.append(_finding("VISUAL_PACKAGE_LINK", f"{name} asset path is not a safe relative path", file=plan_path, location=f"{location}/relative_path", remediation="Use a project-relative path under 03_plan/visual-package/."))
            continue
        target = project_root / relative
        if not require_files and not target.exists():
            continue
        if target.is_symlink() or not target.is_file():
            findings.append(_finding("VISUAL_PACKAGE_FILE_MISSING", f"{name} asset file is missing or not a regular file", file=target, location=f"{location}/relative_path", remediation="Regenerate the visual package into the explicit Git-external output root."))
            continue
        raw = target.read_bytes()
        if sha256_bytes(raw) != asset_record["asset_ref"]["sha256"]:
            findings.append(_finding("VISUAL_PACKAGE_HASH", f"{name} asset hash does not match asset_ref.sha256", file=target, location=f"{location}/asset_ref/sha256", remediation="Restore the generated asset or regenerate the package; never accept a tampered file."))
        if not raw.lstrip().startswith(b"<svg"):
            findings.append(_finding("VISUAL_PACKAGE_VIEWABLE", f"{name} asset is not a viewable SVG fixture", file=target, remediation="Generate a viewable SVG asset without embedding external asset bodies."))
        if b"PRIVATE_RAW" in raw or b"RESTRICTED" in raw:
            findings.append(_finding("VISUAL_PACKAGE_SECURITY", f"{name} asset contains a prohibited data marker", file=target, remediation="Remove prohibited raw or restricted material from the generated asset."))
    source_ids = {item["source_ref_id"] for item in package["source_refs"]}
    if set(package["board"]["source_ref_ids"]) != source_ids:
        findings.append(_finding("VISUAL_PACKAGE_SOURCE_REFERENCE", "board source_ref_ids do not match package source_refs", file=plan_path, location="/visual_package/board/source_ref_ids", remediation="Regenerate the board references from the accepted handoff source references."))
    if package["status"] != "READY" and not package["gaps"]:
        findings.append(_finding("VISUAL_PACKAGE_GAP_RECORD", "non-ready visual package must record an explicit gap", file=plan_path, location="/visual_package/gaps", remediation="Record the missing visual input, observed fact, owner, and release condition."))
    if package["status"] == "READY" and any(item["adoption_status"] == "ADOPTED" and item["rights_status"] in BAD_RIGHTS for item in package["source_refs"]):
        findings.append(_finding("VISUAL_PACKAGE_RIGHTS", "visual package adopts a source with unresolved rights", file=plan_path, location="/visual_package/source_refs", remediation="Reject the source or resolve rights through the human gate before adoption."))
    return findings
