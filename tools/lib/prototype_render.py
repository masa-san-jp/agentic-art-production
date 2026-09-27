"""Render a plan-derived digital prototype through a provider-neutral adapter."""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .canonical import sha256_bytes
from .config import load_config
from .diagnostics import DiagnosticError
from .security import safe_relative_path


OUTPUT_ROOT = "04_prototype/outputs"
MEDIA_TYPE = "image/svg+xml"
EPISTEMIC_STATUS = "simulated"
PREVIEW_ROOT = "03_plan/media/prototype"
RENDERER_CONFIG = "prototype-renderers.yaml"
ALLOWED_UNITS = {"mm", "cm", "m", "g", "kg", "mL", "L", "s", "min", "h", "item", "set", "sheet"}
DIMENSION_UNITS = {"mm", "cm", "m"}


class PrototypeRenderError(ValueError):
    """Raised when the accepted plan does not contain renderable inputs."""


def _renderer_config() -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[2]
    try:
        value = load_config(repository, RENDERER_CONFIG)
    except Exception as exc:
        raise PrototypeRenderError("PROTOTYPE_RENDERER_CONFIG: renderer configuration cannot be read") from exc
    if value.get("version") != 1 or not isinstance(value.get("renderers"), dict):
        raise PrototypeRenderError("PROTOTYPE_RENDERER_CONFIG: renderer configuration is invalid")
    default = value.get("default")
    if not isinstance(default, str) or not default:
        raise PrototypeRenderError("PROTOTYPE_RENDERER_CONFIG: default renderer is missing")
    if default not in value["renderers"]:
        raise PrototypeRenderError("PROTOTYPE_RENDERER_CONFIG: default renderer is not configured")
    return value


def renderer_names() -> list[str]:
    """Return configured renderer names for CLI/help and deterministic previews."""

    return sorted(str(name) for name in _renderer_config()["renderers"])


def default_renderer_name() -> str:
    return str(_renderer_config()["default"])


def _renderer_definition(name: str | None) -> tuple[str, dict[str, Any]]:
    config = _renderer_config()
    renderer_name = name or str(config["default"])
    definition = config["renderers"].get(renderer_name)
    if not isinstance(definition, dict):
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_UNKNOWN: renderer {renderer_name!r} is not configured")
    kind = definition.get("kind")
    if kind not in {"builtin", "command"}:
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_CONFIG: renderer {renderer_name!r} has an invalid kind")
    version = definition.get("version")
    generator = definition.get("generator")
    if not isinstance(version, str) or not version or not isinstance(generator, str) or not generator:
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_CONFIG: renderer {renderer_name!r} lacks identity metadata")
    return renderer_name, definition


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return None


def is_digital_prototype(prototype_plan: dict[str, Any]) -> bool:
    """Return whether a prototype plan is safe for the local renderer."""

    tasks = prototype_plan.get("tasks")
    return prototype_plan.get("executor_capability") == "digital-prototype-renderer" and isinstance(tasks, list) and bool(tasks) and all(
        isinstance(task, dict) and task.get("effect_type") in {"READ_ONLY", "REPOSITORY_WRITE"}
        for task in tasks
    )


def output_relative_path(run_id: str, output_index: int = 1, extension: str = "svg") -> str:
    extension = extension.lstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", extension):
        raise PrototypeRenderError("PROTOTYPE_RENDERER_OUTPUT: output extension is invalid")
    return f"{OUTPUT_ROOT}/{run_id}/prototype-{output_index}.{extension}"


def preview_relative_path(run_id: str, output_index: int = 1, extension: str = "svg") -> str:
    extension = extension.lstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", extension):
        raise PrototypeRenderError("PROTOTYPE_RENDERER_OUTPUT: preview extension is invalid")
    return f"{PREVIEW_ROOT}/{run_id}-{output_index}.{extension}"


def selected_prototype_plans(project_root: Path) -> list[dict[str, Any]]:
    """Load selected prototype plans in the same stable order as the builder."""

    from .yaml_io import load_yaml

    handoff = load_yaml(project_root / "00_handoff/production-handoff.yaml")
    source = load_yaml(project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml")
    if not isinstance(handoff, dict) or not isinstance(source, dict) or not isinstance(source.get("prototype_plans"), list):
        return []
    selected = {str(value) for value in handoff.get("prototype_plan_ids", []) if isinstance(value, str)}
    return sorted(
        [item for item in source["prototype_plans"] if isinstance(item, dict) and str(item.get("id")) in selected],
        key=lambda item: str(item.get("id", "")),
    )


def digital_preview_entries(project_root: Path) -> list[dict[str, Any]]:
    """Return expected digital preview metadata without touching output files."""

    entries = []
    control_path = project_root / "04_prototype/prototype-control.yaml"
    control = None
    if control_path.is_file():
        try:
            from .yaml_io import load_yaml

            candidate = load_yaml(control_path)
            if isinstance(candidate, dict):
                control = candidate
        except (OSError, TypeError, ValueError):
            control = None
    run_by_plan = {
        str(run.get("prototype_plan_id")): run
        for run in (control or {}).get("runs", [])
        if isinstance(run, dict)
    }
    for index, prototype_plan in enumerate(selected_prototype_plans(project_root), start=1):
        if not is_digital_prototype(prototype_plan):
            continue
        run_id = f"PRT{index:03d}"
        run = run_by_plan.get(str(prototype_plan["id"]), {})
        output = next((item for item in run.get("outputs", []) if isinstance(item, dict)), None)
        extension = "svg"
        output_path = output.get("relative_path") if isinstance(output, dict) else None
        if isinstance(output_path, str) and "." in Path(output_path).name:
            extension = Path(output_path).suffix.lstrip(".") or extension
        entries.append({
            "run_id": run_id,
            "prototype_plan_id": str(prototype_plan["id"]),
            "preview_path": preview_relative_path(run_id, extension=extension),
            "output_path": output_path or output_relative_path(run_id, extension=extension),
        })
    return entries


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


def _render_inputs(plan: dict[str, Any], prototype_plan: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "dimensions": dimensions,
        "materials": materials,
        "quantity": quantity,
        "relation": relation,
    }


def _svg(*, plan: dict[str, Any], prototype_plan: dict[str, Any], run_id: str, inputs: dict[str, Any]) -> bytes:
    plan_id = str(plan["plan_id"])
    revision = str(plan["plan_revision"])
    prototype_plan_id = str(prototype_plan["id"])
    dimension_text = ", ".join(f"{item['name']}: {item['quantity']['value']} {item['quantity']['unit']}" for item in inputs["dimensions"])
    material_text = ", ".join(inputs["materials"])
    quantity = inputs["quantity"]
    relation = inputs["relation"]
    prototype_inputs = [value for value in (_text(item) for item in prototype_plan.get("inputs", [])) if value]
    constraints = [value for value in (_text(item) for item in prototype_plan.get("constraints", [])) if value]
    plan_input_text = "; ".join(prototype_inputs) or "accepted production-plan inputs"
    constraint_text = "; ".join(constraints) or "no additional prototype constraint"
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
<text x="48" y="728" font-size="12" fill="#475569" font-family="sans-serif">Prototype inputs: {_xml(plan_input_text)}</text>
<text x="48" y="748" font-size="12" fill="#475569" font-family="sans-serif">Constraints: {_xml(constraint_text)}</text>
</svg>
'''.encode("utf-8")


def _renderer_identity(renderer_name: str, definition: dict[str, Any]) -> dict[str, str]:
    return {
        "name": renderer_name,
        "version": str(definition["version"]),
        "generator_id": str(definition["generator"]),
        "adapter": str(definition["kind"]),
    }


def _command_argv(renderer_name: str, definition: dict[str, Any]) -> tuple[str, ...]:
    command = definition.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item or "\x00" in item for item in command):
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_CONFIG: renderer {renderer_name!r} command must be a non-empty argv list")
    shell_names = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}
    if Path(command[0]).name.lower() in shell_names or any(re.search(r"[;&|$`()<>`\n\r]", item) for item in command):
        raise PrototypeRenderError("PROTOTYPE_RENDERER_COMMAND: shell commands and metacharacters are not permitted")
    repository = Path(__file__).resolve().parents[2]
    resolved = [str(repository / item) if not Path(item).is_absolute() and (repository / item).is_file() else item for item in command]
    if Path(resolved[0]).name in {"python", "python3", "python3.11"}:
        resolved[0] = sys.executable
    return tuple(resolved)


def _command_render(
    *,
    project_root: Path,
    request: dict[str, Any],
    renderer_name: str,
    definition: dict[str, Any],
) -> tuple[bytes, str, str]:
    argv = _command_argv(renderer_name, definition)
    timeout = definition.get("timeout_seconds", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise PrototypeRenderError("PROTOTYPE_RENDERER_CONFIG: timeout_seconds must be positive")
    max_stdout = definition.get("max_stdout_bytes", 65536)
    max_stderr = definition.get("max_stderr_bytes", 16384)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (max_stdout, max_stderr)):
        raise PrototypeRenderError("PROTOTYPE_RENDERER_CONFIG: output limits must be positive integers")
    environment = {"PATH": os.environ.get("PATH", "")}
    for key in definition.get("environment_allowlist", []):
        if not isinstance(key, str) or not key:
            raise PrototypeRenderError("PROTOTYPE_RENDERER_CONFIG: environment_allowlist is invalid")
        if key in os.environ:
            environment[key] = os.environ[key]
    payload = (json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        completed = subprocess.run(
            list(argv), input=payload, capture_output=True, cwd=project_root, env=environment,
            shell=False, timeout=float(timeout), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_TIMEOUT: renderer {renderer_name!r} exceeded {timeout} seconds") from exc
    except (OSError, ValueError) as exc:
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_START: renderer {renderer_name!r} could not start") from exc
    if len(completed.stdout) > max_stdout or len(completed.stderr) > max_stderr:
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_OUTPUT_LIMIT: renderer {renderer_name!r} exceeded stdout/stderr limits")
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {stderr[:300]}" if stderr else ""
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_EXIT: renderer {renderer_name!r} exited {completed.returncode}{detail}")
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrototypeRenderError(f"PROTOTYPE_RENDERER_PROTOCOL: renderer {renderer_name!r} must return one JSON result on stdout") from exc
    if not isinstance(result, dict):
        raise PrototypeRenderError("PROTOTYPE_RENDERER_PROTOCOL: renderer result must be a JSON object")
    relative_path = result.get("output_relative_path")
    media_type = result.get("media_type")
    generator_id = result.get("generator_id")
    if not isinstance(relative_path, str) or not isinstance(media_type, str) or not isinstance(generator_id, str) or not generator_id:
        raise PrototypeRenderError("PROTOTYPE_RENDERER_PROTOCOL: result requires output_relative_path, media_type, and generator_id")
    try:
        safe_relative_path(relative_path)
    except (DiagnosticError, TypeError, ValueError) as exc:
        raise PrototypeRenderError("PROTOTYPE_RENDERER_PROTOCOL: output_relative_path must be safe and relative") from exc
    expected_prefix = f"{OUTPUT_ROOT}/{request['run_id']}/"
    if not relative_path.startswith(expected_prefix) or Path(relative_path).suffix == "":
        raise PrototypeRenderError("PROTOTYPE_RENDERER_PROTOCOL: output path must stay in the requested run directory")
    if not media_type.startswith("image/"):
        raise PrototypeRenderError("PROTOTYPE_RENDERER_PROTOCOL: media_type must be an image media type")
    configured_media_type = definition.get("media_type")
    if configured_media_type is not None and media_type != configured_media_type:
        raise PrototypeRenderError("PROTOTYPE_RENDERER_PROTOCOL: result media_type does not match renderer configuration")
    target = project_root / relative_path
    if target.is_symlink() or not target.is_file():
        raise PrototypeRenderError("PROTOTYPE_RENDERER_OUTPUT: renderer did not create the declared output file")
    return target.read_bytes(), relative_path, generator_id


def render_digital_prototype(*, project_root: Path, plan: dict[str, Any], prototype_plan: dict[str, Any], run_id: str, output_index: int = 1, renderer_name: str | None = None) -> tuple[dict[str, Any], bytes]:
    """Return an output record and bytes from the configured prototype renderer."""
    if (project_root / ".git").exists():
        raise PrototypeRenderError("project root must be Git-external so artwork bytes do not enter a repository")
    if not is_digital_prototype(prototype_plan):
        raise PrototypeRenderError("prototype plan is not eligible for the local digital renderer")
    inputs = _render_inputs(plan, prototype_plan)
    selected_renderer, definition = _renderer_definition(renderer_name)
    identity = _renderer_identity(selected_renderer, definition)
    if definition["kind"] == "builtin":
        if selected_renderer != "deterministic-svg":
            raise PrototypeRenderError(f"PROTOTYPE_RENDERER_BUILTIN: unsupported builtin renderer {selected_renderer!r}")
        relative_path = output_relative_path(run_id, output_index, "svg")
        raw = _svg(plan=plan, prototype_plan=prototype_plan, run_id=run_id, inputs=inputs)
    else:
        relative_path_hint = output_relative_path(run_id, output_index, str(definition.get("extension", "bin")))
        request = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "output_index": output_index,
            "output_relative_path": relative_path_hint,
            "plan": {
                "id": str(plan["plan_id"]),
                "revision": int(plan["plan_revision"]),
                "content_sha256": str(plan["integrity"]["content_sha256"]),
            },
            "prototype_plan_id": str(prototype_plan["id"]),
            "inputs": inputs,
            "epistemic_status": EPISTEMIC_STATUS,
            "not_physical_evidence": True,
        }
        raw, relative_path, generator_id = _command_render(project_root=project_root, request=request, renderer_name=selected_renderer, definition=definition)
        identity["generator_id"] = generator_id
    safe_relative_path(relative_path)
    suffix = Path(relative_path).suffix.lstrip(".")
    if not suffix:
        raise PrototypeRenderError("PROTOTYPE_RENDERER_OUTPUT: output path must have an extension")
    output = {
        "run_id": run_id,
        "prototype_plan_id": str(prototype_plan["id"]),
        "relative_path": relative_path,
        "media_type": str(definition.get("media_type", MEDIA_TYPE)),
        "sha256": sha256_bytes(raw),
        "byte_length": len(raw),
        "epistemic_status": EPISTEMIC_STATUS,
        "plan_id": str(plan["plan_id"]),
        "plan_revision": int(plan["plan_revision"]),
        "plan_sha256": str(plan["integrity"]["content_sha256"]),
        "renderer": identity,
        "not_physical_evidence": True,
        "dimensions": inputs["dimensions"],
        "materials": inputs["materials"],
        "quantity": inputs["quantity"],
        "scale_note": "NOT TO SCALE; simulated digital preview only.",
        "viewer_installation_relation": inputs["relation"],
    }
    return output, raw
