#!/usr/bin/env python3
"""Verify a canonical plan and its concrete production method, without authorizing work."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.lib.actionability import assess
from tools.lib.yaml_io import load_yaml
from tools.validate import validate_project
from tools import build_plan
ROOT = Path(__file__).resolve().parents[1]


def verify(project):
    project = Path(project).resolve()
    plan = load_yaml(project / '03_plan/production-plan.yaml')
    result = assess(plan, ROOT)
    result['findings'].extend(f.rule + ': ' + f.reason for f in validate_project(project, ROOT))
    if (project / '03_plan/production-plan.md').read_bytes() != build_plan._render_human_plan(project, plan).encode('utf-8'):
        result['findings'].append('CANONICAL_RENDERER_MISMATCH')
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
