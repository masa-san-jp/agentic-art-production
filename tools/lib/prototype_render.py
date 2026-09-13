"""Render a deterministic, local-only digital prototype preview."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from .canonical import sha256_bytes
from .security import safe_relative_path


OUTPUT_ROOT = "04_prototype/outputs"
MEDIA_TYPE = "image/svg+xml"
EPISTEMIC_STATUS = "simulated"
ALLOWED_UNITS = {"mm", "cm", "m", "g", "kg", "mL", "L", "s", "min", "h", "item", "set", "sheet"}
DIMENSION_UNITS = {"mm", "cm", "m"}


class PrototypeRenderError(ValueError):
    """Raised when the accepted plan does not contain renderable inputs."""


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return None


def _xml(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _quantity(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    number = value.get("value")
    unit = value.get("unit")
    if isinstance(number, (str, int, float)) and not isinstance(number, bool) and isinstance(unit, str) and unit in ALLOWED_UNITS:
        return {"value": str(number), "unit": unit}
    return None


def _dimension_records(plan: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for specification in plan.get("technical_specifications", []):
        if not isinstance(specification, dict):
            continue
        target = specification.get("target")
        if not isinstance(target, dict):
            continue
        quantity = _quantity(target.get("quantity"))
        if quantity is None or quantity["unit"] not in DIMENSION_UNITS:
            continue
        name = _text(specification.get("parameter")) or _text(target.get("statement"))
        if name:
            records.append({"name": name, "quantity": quantity})
    if records:
        return records

    # Compatibility for plans that expose one explicit numeric dimension in
    # the generated visual package while still requiring a unit.
    mockup = ((plan.get("visual_package") or {}).get("mockup") or {})
    dimensions = _text(mockup.get("dimensions"))
    if dimensions:
        match = re.search(r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>mm|cm|m)\b", dimensions)
        if match:
            return [{"name": "visual-package-dimension", "quantity": {"value": match.group("value"), "unit": match.group("unit")}}]
    return []


def _material_records(plan: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for material in plan.get("materials", []):
        if not isinstance(material, dict):
            continue
        name = _text(material.get("name"))
        if name:
            values.append(name)
    return list(dict.fromkeys(values))


def _object_quantity(plan: dict[str, Any]) -> dict[str, str] | None:
    for material in plan.get("materials", []):
        if isinstance(material, dict):
            quantity = _quantity(material.get("quantity"))
            if quantity and quantity["unit"] in {"item", "set", "sheet"}:
                return quantity
    for specification in plan.get("technical_specifications", []):
        if isinstance(specification, dict):
            target = specification.get("target")
            quantity = _quantity(target.get("quantity")) if isinstance(target, dict) else None
            if quantity and quantity["unit"] in {"item", "set", "sheet"}:
                return quantity
    return None


def _render_inputs(plan: dict[str, Any]) -> dict[str, Any]:
    dimensions = _dimension_records(plan)
    materials = _material_records(plan)
    quantity = _object_quantity(plan)
    mockup = ((plan.get("visual_package") or {}).get("mockup") or {})
    relation = _text(mockup.get("placement"))
    missing = []
    if not dimensions:
        missing.append("numeric dimensions with a supported unit")
    if not materials:
        missing.append("at least one material name")
    if quantity is None:
        missing.append("object quantity with item, set, or sheet unit")
    if not relation:
        missing.append("viewer/installation relation")
    if missing:
        raise PrototypeRenderError("missing " + ", ".join(missing))
    return {"dimensions": dimensions, "materials": materials, "quantity": quantity, "relation": relation}


def _svg(*, plan: dict[str, Any], prototype_plan: dict[str, Any], run_id: str, inputs: dict[str, Any]) -> bytes:
    plan_id = str(plan["plan_id"])
    revision = str(plan["plan_revision"])
    prototype_plan_id = str(prototype_plan["id"])
    dimension_text = ", ".join(f"{item['name']}: {item['quantity']['value']} {item['quantity']['unit']}" for item in inputs["dimensions"])
    material_text = ", ".join(inputs["materials"])
    quantity = inputs["quantity"]
    relation = inputs["relation"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760">
<title>Digital prototype {_xml(run_id)}</title>
<desc>Deterministic simulated digital prototype. No physical, network, provider, purchase, or publication action was performed.</desc>
<rect width="1200" height="760" fill="#f8fafc" />
<rect x="90" y="160" width="760" height="370" rx="16" fill="#ffffff" stroke="#334155" stroke-width="4" />
<rect x="270" y="235" width="400" height="220" fill="#cbd5e1" stroke="#111827" stroke-width="5" />
<line x1="270" y1="475" x2="670" y2="475" stroke="#f59e0b" stroke-width="5" stroke-dasharray="10 8" />
<line x1="135" y1="600" x2="805" y2="600" stroke="#64748b" stroke-width="4" />
<circle cx="150" cy="575" r="15" fill="#f59e0b" />
<line x1="150" y1="575" x2="270" y2="350" stroke="#f59e0b" stroke-width="3" stroke-dasharray="8 8" />
<text x="48" y="62" font-size="30" fill="#111827" font-family="sans-serif">Digital prototype · {_xml(run_id)}</text>
<text x="48" y="98" font-size="15" fill="#475569" font-family="sans-serif">{_xml(plan_id)} revision {_xml(revision)} · {_xml(prototype_plan_id)} · SIMULATED · NOT TO SCALE</text>
<text x="48" y="132" font-size="13" fill="#475569" font-family="sans-serif">SVG generated locally from the accepted production plan; output is inspectable and not physical evidence.</text>
<text x="900" y="190" font-size="16" font-weight="bold" fill="#111827" font-family="sans-serif">PLAN INPUTS</text>
<text x="900" y="225" font-size="13" fill="#475569" font-family="sans-serif">DIMENSIONS</text>
<text x="900" y="250" font-size="13" fill="#111827" font-family="sans-serif">{_xml(dimension_text)}</text>
<text x="900" y="295" font-size="13" fill="#475569" font-family="sans-serif">MATERIAL</text>
<text x="900" y="320" font-size="13" fill="#111827" font-family="sans-serif">{_xml(material_text)}</text>
<text x="900" y="365" font-size="13" fill="#475569" font-family="sans-serif">QUANTITY</text>
<text x="900" y="390" font-size="13" fill="#111827" font-family="sans-serif">{_xml(quantity['value'])} {_xml(quantity['unit'])}</text>
<text x="900" y="435" font-size="13" fill="#475569" font-family="sans-serif">VIEWER / INSTALLATION</text>
<text x="900" y="460" font-size="13" fill="#111827" font-family="sans-serif">{_xml(relation)}</text>
<text x="48" y="660" font-size="14" fill="#111827" font-family="sans-serif">Viewer position → object outline → installation surface relation</text>
<text x="48" y="700" font-size="13" fill="#475569" font-family="sans-serif">Scale: NOT TO SCALE · epistemic status: simulated · run: {_xml(run_id)}</text>
</svg>
'''.encode("utf-8")


def render_digital_prototype(*, project_root: Path, plan: dict[str, Any], prototype_plan: dict[str, Any], run_id: str) -> tuple[dict[str, Any], bytes]:
    """Return a deterministic output record and SVG bytes without writing files."""
    if (project_root / ".git").exists():
        raise PrototypeRenderError("project root must be Git-external so artwork bytes do not enter a repository")
    inputs = _render_inputs(plan)
    relative_path = f"{OUTPUT_ROOT}/{run_id}/{run_id}-1.svg"
    safe_relative_path(relative_path)
    raw = _svg(plan=plan, prototype_plan=prototype_plan, run_id=run_id, inputs=inputs)
    output = {
        "run_id": run_id,
        "prototype_plan_id": str(prototype_plan["id"]),
        "relative_path": relative_path,
        "media_type": MEDIA_TYPE,
        "sha256": sha256_bytes(raw),
        "byte_length": len(raw),
        "epistemic_status": EPISTEMIC_STATUS,
        "dimensions": inputs["dimensions"],
        "materials": inputs["materials"],
        "quantity": inputs["quantity"],
        "scale_note": "NOT TO SCALE; simulated digital preview only.",
        "viewer_installation_relation": inputs["relation"],
    }
    return output, raw
