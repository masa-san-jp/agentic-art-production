#!/usr/bin/env python3
"""Verify a canonical plan and its concrete production method, without authorizing work."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.lib.actionability import assess
from tools.lib.canonical import sha256_bytes
from tools.lib.prototype_render import digital_preview_entries
from tools.lib.yaml_io import load_yaml
from tools.validate import validate_project
from tools import build_plan
ROOT = Path(__file__).resolve().parents[1]


def _check_digital_previews(project: Path, body: str, result: dict) -> None:
    """Verify the digital preview, its source output, and canonical body link."""

    entries = digital_preview_entries(project)
    result["prototype_status"] = "READY"
    if not entries:
        return
    result["prototype_status"] = "MISSING"
    try:
        control = load_yaml(project / "04_prototype/prototype-control.yaml")
    except (OSError, ValueError, TypeError):
        control = None
    runs = control.get("runs", []) if isinstance(control, dict) else []
    run_by_id = {str(run.get("id")): run for run in runs if isinstance(run, dict)}
    failures = []
    for entry in entries:
        run = run_by_id.get(entry["run_id"])
        output = None
        if run:
            output = next((item for item in run.get("outputs", []) if isinstance(item, dict)), None)
        preview_path = project / entry["preview_path"]
        output_path = project / entry["output_path"]
        expected_link = Path(entry["preview_path"]).relative_to("03_plan").as_posix()
        if f"]({expected_link})" not in body:
            failures.append(f"PROTOTYPE_OUTPUT_MISSING: canonical body link is missing for {entry['run_id']}")
        if not isinstance(output, dict) or output.get("relative_path") != entry["output_path"]:
            failures.append(f"PROTOTYPE_OUTPUT_MISSING: output record is missing for {entry['run_id']}")
            continue
        if output_path.is_symlink() or not output_path.is_file():
            failures.append(f"PROTOTYPE_OUTPUT_MISSING: source output file is missing for {entry['run_id']}")
            continue
        source_raw = output_path.read_bytes()
        if sha256_bytes(source_raw) != output.get("sha256") or len(source_raw) != output.get("byte_length"):
            failures.append(f"PROTOTYPE_OUTPUT_MISSING: source output hash is inconsistent for {entry['run_id']}")
            continue
        if preview_path.is_symlink() or not preview_path.is_file():
            failures.append(f"PROTOTYPE_OUTPUT_MISSING: public preview file is missing for {entry['run_id']}")
            continue
        if preview_path.read_bytes() != source_raw:
            failures.append(f"PROTOTYPE_OUTPUT_MISSING: public preview hash is inconsistent for {entry['run_id']}")
    if failures:
        result["findings"].extend(failures)
    else:
        result["prototype_status"] = "READY"


def verify(project):
    project = Path(project).resolve()
    plan = load_yaml(project / '03_plan/production-plan.yaml')
    result = assess(plan, ROOT)
    result['findings'].extend(f.rule + ': ' + f.reason for f in validate_project(project, ROOT))
    body_path = project / '03_plan/production-plan.md'
    body = body_path.read_text(encoding='utf-8') if body_path.is_file() else ''
    if not body_path.is_file() or body_path.read_bytes() != build_plan._render_human_plan(project, plan).encode('utf-8'):
        result['findings'].append('CANONICAL_RENDERER_MISMATCH')
    _check_digital_previews(project, body, result)
    path = project / '02_specification/production-method.yaml'
    if path.is_symlink() or not path.is_file():
        result['findings'].append('METHOD_INPUT_MISMATCH')
    if result['findings']:
        result['plan_status'] = 'INCOMPLETE'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.project_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'plan_status': 'INCOMPLETE', 'findings': [str(exc)], 'external_effects_authorized': False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['plan_status'] == 'PLAN_READY' else 1

if __name__ == '__main__':
    raise SystemExit(main())
