#!/usr/bin/env python3
"""Stage D: the production method from an accepted handoff, one request and one answer at a time.

The same wire contracts as Research Stages B and C (`element-request/v1` / `element-answer/v1`).
The engine pins the accepted handoff and the plan it derives without a method, asks one value per
request, checks it by program, writes `02_specification/production-method.yaml` once every element
is answered, rebuilds the plan with `build_plan.py` and judges it with `plan_actionability`. A
finding that names an element re-asks that element alone. It never calls a model or the network
and never authorizes physical, purchase or publication work.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import build_plan, plan_actionability
from tools.lib.config import load_config
from tools.lib.canonical import handoff_sha256
from tools.lib.plan_element_checks import check_value
from tools.lib.plan_element_driver import abandonable, advance, element_for_finding
from tools.lib.yaml_io import load_yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 'plan-elements/v1'
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('--now must carry a time zone')
    return parsed


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(text: str) -> str:
    return 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()


def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def work_snapshot(project: Path, plan: dict) -> dict:
    """The work facts requests may carry, read from the accepted handoff bundle (program only)."""
    handoff, _, _, artifacts = build_plan._load_inputs(project)
    selected = str((handoff.get('selection') or {}).get('selected_hypothesis_id'))
    hypothesis = next((h for h in artifacts['hypotheses'].get('hypotheses', []) if str(h.get('id')) == selected), {})
    brief = artifacts.get('production_brief') or {}
    concept = brief.get('concept') if isinstance(brief.get('concept'), dict) else {}
    includes = [str(item) for item in hypothesis.get('includes', []) if str(item).strip()]
    feasibility = hypothesis.get('feasibility') if isinstance(hypothesis.get('feasibility'), dict) else {}
    requirements = [r for r in artifacts['requirements'].get('requirements', []) if r.get('priority') == 'mandatory']
    return {
        'hypothesis_id': selected, 'work_title': str(hypothesis.get('title') or selected),
        'proposition': str(hypothesis.get('proposition') or ''),
        # Stage C writes the components first and the material sentence last.
        'components': includes[:-1] if len(includes) > 1 else includes,
        'material': includes[-1] if includes else '',
        'mechanism': str(concept.get('mechanism') or ''),
        'venue_dependency': str(feasibility.get('venue_dependency') or 'UNKNOWN'),
        'requirements': [f"{r['id']}: {r.get('statement', '')}" for r in requirements],
        'acceptance_tests': [{'id': str(t['id']), 'target_requirement': str(t.get('target_requirement'))}
                             for t in artifacts['acceptance_tests'].get('acceptance_tests', [])],
    }


class PlanEngine:
    """A locked checkpoint owns all Stage D values; the method file is written when every element passed."""

    def __init__(self, project: Path):
        self.project = Path(project).resolve()
        if not (self.project / '00_handoff/production-handoff.yaml').is_file():
            raise ValueError('Stage D needs an accepted handoff (run new_production.py first)')
        self.config = load_config(ROOT, 'plan-elements.yaml')
        self.policy = load_config(ROOT, 'plan-actionability.yaml')
        self.path = self.safe(self.config['paths']['state'])

    def safe(self, relative: str) -> Path:
        path = (self.project / relative)
        if path.is_symlink() or self.project not in path.resolve().parents:
            raise ValueError(f'unsafe project path: {relative}')
        return path

    @contextmanager
    def locked(self):
        lock = self.safe(self.config['paths']['lock'])
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def handoff_hash(self) -> str:
        handoff = load_yaml(self.project / '00_handoff/production-handoff.yaml')
        integrity = handoff.get('integrity') if isinstance(handoff, dict) else None
        if not isinstance(integrity, dict) or not isinstance(integrity.get('content_sha256'), str):
            raise ValueError('accepted handoff has no integrity hash')
        if handoff_sha256(handoff) != integrity['content_sha256']:
            raise ValueError('The accepted handoff changed: its content does not match the integrity hash')
        return integrity['content_sha256']

    def input_hash(self) -> str:
        """Pin the complete accepted bundle, not only its self-reported hash."""
        root = self.project / '00_handoff'
        files = {}
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise ValueError('The accepted handoff contains a symlink')
            if path.is_file():
                files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest(canonical(files))

    def initialize(self, now: str, run_id: str) -> dict:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError('Unsafe run ID')
        method_path = self.safe(self.config['paths']['method'])
        if method_path.exists():
            raise ValueError(f"{self.config['paths']['method']} already exists; Stage D starts only in a project without a method")
        plan = build_plan._build_plan(self.project, method=None)
        base = {'handoff_sha256': self.handoff_hash(), 'input_sha256': self.input_hash(),
                'plan_sha256': plan['integrity']['content_sha256'],
                'mandatory_requirement_ids': list(plan['mandatory_requirement_ids']),
                'material_ids': [m['id'] for m in plan['materials']], 'resource_ids': [r['id'] for r in plan['resources']],
                'tasks': [{'id': t['id'], 'title': t['title'], 'effect_type': t['effect_type'], 'depends_on': list(t['depends_on']),
                           'acceptance_condition': t['acceptance_condition'],
                           'required_material_ids': list(t.get('required_material_ids', [])),
                           'required_resource_ids': list(t.get('required_resource_ids', [])),
                           'requirement_ids': list(t.get('requirement_ids', [])), 'trace_refs': list(t.get('trace_refs', []))}
                          for t in plan['tasks']]}
        return {'contract_version': CONTRACT, 'run_id': run_id, 'project_root_name': self.project.name,
                'started_at': now, 'last_now': now, 'config': deepcopy(self.config),
                'policy': {k: deepcopy(self.policy[k]) for k in ('required_specifications', 'numeric_specifications',
                                                                 'placeholder_fragments', 'placeholder_values',
                                                                 'specification_patterns') if k in self.policy},
                'base': base, 'work': work_snapshot(self.project, plan),
                'units': list(load_config(ROOT, 'units.yaml')['units']), 'answers': {}, 'abandoned': {}, 'history': [],
                'attempts': {}, 'failures': {}, 'status': 'WAITING', 'pending': None, 'files': {}, 'completion': None}

    def read(self) -> dict:
        state = json.loads(self.path.read_text(encoding='utf-8'))
        if state.get('contract_version') != CONTRACT:
            raise ValueError('Unsupported Stage D checkpoint')
        if self.handoff_hash() != state['base']['handoff_sha256']:
            raise ValueError('The accepted handoff changed since Stage D started; start Stage D in a fresh project')
        if self.input_hash() != state['base'].get('input_sha256'):
            raise ValueError('The accepted handoff changed since Stage D started; start Stage D in a fresh project')
        plan = build_plan._build_plan(self.project, method=None)
        if plan['integrity']['content_sha256'] != state['base']['plan_sha256']:
            raise ValueError('The base plan changed since Stage D started; start Stage D in a fresh project')
        return state

    def save(self, state: dict) -> None:
        atomic_write(self.path, stable_json(state))
        os.chmod(self.path, 0o600)

    def materialize(self, state: dict) -> None:
        for relative, content in state['files'].items():
            path = self.safe(relative)
            actual = path.read_text(encoding='utf-8') if path.exists() else None
            if actual not in (None, content):
                raise ValueError(f'{relative}: changed outside the Stage D element engine')
            if actual is None:
                atomic_write(path, content)

    def probe(self, method: dict) -> dict:
        return build_plan._build_plan(self.project, method=method)

    def advance(self, state: dict) -> None:
        """Ask the next element; once none is left, write the method, rebuild and judge the plan."""
        for _ in range(state['config']['max_attempts'] * 4 + 4):
            method = advance(state, self.probe)
            if method is None or state['status'] == 'BLOCKED':
                state['status'] = 'BLOCKED' if state['status'] == 'BLOCKED' else 'WAITING'
                return
            retry = self.finish(state, method)
            if not retry:
                return
        raise ValueError('Stage D could not settle the actionability retries')

    def finish(self, state: dict, method: dict) -> bool:
        """Write the method and plan, judge it, and re-ask any element a finding names. True if re-asked."""
        relative = state['config']['paths']['method']
        text = dump_text(method)
        state['files'] = {relative: text}
        state['method_owners'] = {record['id']: record['task_ids'][0]
                                  for kind in ('materials', 'resources') for record in method.get(kind, [])}
        atomic_write(self.safe(relative), text)
        build = io.StringIO()
        with redirect_stdout(build):
            built = build_plan.main(['--project-root', str(self.project), '--format', 'json'])
        result = None
        if built != 0:
            result = {'plan_status': 'INCOMPLETE', 'findings': ['PLAN_BUILD: ' + build.getvalue().strip()[-600:]],
                      'first_task_id': None}
        else:
            # Prototype rendering is program work too: the selected digital plan is drawn from the
            # plan's dimensions, materials and quantity before the plan is judged.
            from tools import build_prototype
            render = io.StringIO()
            with redirect_stdout(render):
                rendered = build_prototype.main(['--project-root', str(self.project), '--format', 'json'])
            if rendered != 0:
                result = {'plan_status': 'INCOMPLETE', 'findings': ['PROTOTYPE_BUILD: ' + render.getvalue().strip()[-600:]],
                          'first_task_id': None}
        if result is None:
            try:
                result = plan_actionability.verify(self.project)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result = {'plan_status': 'INCOMPLETE', 'findings': [f'PLAN_VERIFY: {exc}'], 'first_task_id': None}
        retried = []
        for finding in result['findings']:
            identifier = element_for_finding(finding, state)
            if identifier and identifier in state['answers']:
                retried.append(identifier)
                self.reject(state, identifier, [{'check': 'actionability', 'reason': finding}])
        if retried and state['status'] != 'BLOCKED':
            return True
        state['status'] = 'BLOCKED' if state['status'] == 'BLOCKED' else 'COMPLETED'
        state['pending'] = None
        state['completion'] = {
            'stage': 'D', 'plan_status': result['plan_status'], 'findings': list(result['findings']),
            'first_task_id': result.get('first_task_id'), 'method': relative,
            'method_sha256': digest(text), 'abandoned': sorted(state['abandoned']),
            'external_effects_authorized': False,
            'next_start': 'tools/build_prototype.py and tools/plan_actionability.py on this project.'}
        return False

    def reject(self, state: dict, identifier: str, failures: list) -> None:
        """Count one failed attempt of an element; skip, block or re-ask it."""
        attempt = state['attempts'].get(identifier, 1)
        state['answers'].pop(identifier, None)
        if attempt >= state['config']['max_attempts']:
            if abandonable(state, identifier):
                state['abandoned'][identifier] = failures
                state['failures'].pop(identifier, None)
            else:
                state.update(status='BLOCKED', pending=None,
                             blocked={'element_id': identifier, 'attempt': attempt, 'last_failure': failures})
            return
        state['attempts'][identifier] = attempt + 1
        state['failures'][identifier] = failures

    def report(self, state: dict) -> dict:
        return {'run_id': state['run_id'], 'stage': 'D', 'answer_with': 'tools/plan_elements.py answer',
                'status': state['status'], 'accepted_count': len(state['answers']),
                'abandoned': sorted(state['abandoned']), 'blocked': state.get('blocked'),
                'completion': state.get('completion'),
                'next_action': {'kind': 'element', 'request': deepcopy(state['pending'])} if state['pending'] else None}

    def check_time(self, state: dict, now: str) -> None:
        if timestamp(now) < timestamp(state['last_now']):
            raise ValueError('--now cannot move backwards')
        state['last_now'] = now

    def next(self, *, now: str, run_id: str = 'plan') -> dict:
        timestamp(now)
        with self.locked():
            if self.path.exists():
                state = self.read()
                self.materialize(state)
                self.check_time(state, now)
            else:
                state = self.initialize(now, run_id)
                self.advance(state)
            self.save(state)
            return self.report(state)

    def answer(self, answer: dict, *, now: str) -> dict:
        timestamp(now)
        with self.locked():
            state = self.read()
            self.materialize(state)
            self.check_time(state, now)
            answer_hash = digest(canonical(answer))
            if any(item['answer_hash'] == answer_hash for item in state['history']):
                self.save(state)
                return self.report(state)  # A resent answer is not a second attempt.
            if state['status'] != 'WAITING':
                raise ValueError('Run is not waiting for an answer')
            request = state['pending']
            require_answer(answer)
            if any(answer[key] != request[key] for key in ('run_id', 'element_id', 'attempt')):
                raise ValueError('Answer does not match pending identity/attempt')
            failures = check_value(request, answer['value'], state['policy'])
            identifier = request['element_id']
            state['history'].append({'element_id': identifier, 'attempt': request['attempt'],
                                     'answer_hash': answer_hash, 'occurred_at': now, 'failures': failures})
            if failures:
                self.reject(state, identifier, failures)
            else:
                state['answers'][identifier] = answer['value']
                state['failures'].pop(identifier, None)
            if state['status'] != 'BLOCKED':
                self.advance(state)
            self.save(state)
            return self.report(state)


def dump_text(value) -> str:
    buffer = io.StringIO()
    import yaml
    yaml.safe_dump(value, buffer, allow_unicode=True, sort_keys=False)
    return buffer.getvalue()


def require_answer(answer) -> None:
    if not isinstance(answer, dict) or answer.get('contract_version') != 'element-answer/v1':
        raise ValueError('Answer must be an element-answer/v1 object')
    extra = set(answer) - {'contract_version', 'run_id', 'element_id', 'attempt', 'value'}
    if extra:
        raise ValueError('Answer has unexpected fields: ' + ', '.join(sorted(extra)))
    if not isinstance(answer.get('attempt'), int) or not isinstance(answer.get('element_id'), str):
        raise ValueError('Answer needs element_id and an integer attempt')
    if not isinstance(answer.get('value'), str):
        raise ValueError('Stage D values are strings (text or one listed choice)')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['next', 'answer'])
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--run-id', default='plan', help='identity for a new Stage D run')
    parser.add_argument('--now', help='RFC3339; defaults to current UTC time')
    args = parser.parse_args(argv)
    try:
        now = args.now or datetime.now(timezone.utc).isoformat(timespec='seconds')
        engine = PlanEngine(args.project_root)
        if args.command == 'next':
            result = engine.next(now=now, run_id=args.run_id)
        else:
            raw = sys.stdin.buffer.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError('Answer envelope exceeds input byte budget')
            result = engine.answer(json.loads(raw), now=now)
    except (ValueError, OSError, KeyError) as exc:
        print(f'plan-elements: {exc}', file=sys.stderr)
        return 2
    print(stable_json(result), end='')
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
