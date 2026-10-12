"""Deterministic Stage D scheduling and method assembly. No model or network calls.

Stage D turns an accepted handoff into the proposed production method one value at a time:
medium and environment, each specification value, each task's procedure sentence and completion
check (with the material and tool of every physical task), then how to check and when to consider
each plan gap resolved. Process, schedule, budget, wiring and the actionability judgement are the
existing program (`build_plan.py`, `lib/actionability.py`).
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Iterator

from .plan_element_checks import quantity

LOCAL_EFFECTS = {'READ_ONLY', 'REPOSITORY_WRITE'}


def fit(limit: int, inputs: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    """Keep a request inside the byte budget: trim optional lists, then shorten the longest strings."""
    inputs = deepcopy(inputs)

    def size() -> int:
        return len(json.dumps(inputs, ensure_ascii=False).encode())
    for key in optional:
        value = inputs.get(key)
        while isinstance(value, list) and value and size() > limit:
            value.pop()
        if size() > limit:
            inputs.pop(key, None)
    while size() > limit:
        strings = [k for k, v in inputs.items() if isinstance(v, str) and v]
        if not strings:
            raise ValueError('Stage D inputs exceed the configured request byte budget')
        key = max(strings, key=lambda k: len(inputs[k].encode()))
        raw = inputs[key].encode('utf-8')
        inputs[key] = raw[:max(0, len(raw) - max(3, size() - limit))].decode('utf-8', errors='ignore')
    return inputs


def element(state, identifier, instruction, inputs, *, optional=(), choices=None, checks=(), min_chars=1, max_chars=240):
    fmt = {'type': 'choice', 'choices': list(choices)} if choices else {
        'type': 'text', 'min_chars': min_chars, 'max_chars': max_chars}
    all_checks = ['non_empty', *([] if choices else ['not_placeholder']), *checks]
    if 'one_sentence' in all_checks:
        instruction += '1文で返してください。'
    return {'contract_version': 'element-request/v1', 'run_id': state['run_id'], 'element_id': identifier,
            'attempt': state['attempts'].get(identifier, 1), 'previous_failure': state['failures'].get(identifier),
            'instruction': instruction, 'inputs': fit(state['config']['input_bytes'], inputs, optional),
            'answer_format': fmt, 'checks': all_checks}


def ask(state, identifier, instruction, inputs, **kwargs) -> Iterator[dict]:
    """Yield one request; return the accepted value, or None when the element was abandoned."""
    yield element(state, identifier, instruction, inputs, **kwargs)
    if identifier in state['abandoned']:
        return None
    return state['answers'][identifier]


def abandonable(state, identifier: str) -> bool:
    """Skipping must not make the whole run unreasonable to finish.

    Local (READ_ONLY / REPOSITORY_WRITE) task procedures and non-blocking gap checks may be skipped: the
    plan then stays INCOMPLETE with the finding recorded. Medium, environment, specifications and
    the procedure, material and tool of an external task block the run instead.
    """
    if identifier.startswith('gap.'):
        gap_id = identifier.split('.')[1]
        return state.get('gap_policies', {}).get(gap_id) is False
    if identifier.startswith('step.'):
        task_id = identifier.split('.')[1]
        task = next((t for t in state['base']['tasks'] if t['id'] == task_id), None)
        return task is not None and task['effect_type'] in LOCAL_EFFECTS
    return False


def requirement_ids(task, mandatory):
    refs = task.get('requirement_ids') or task.get('trace_refs', [])
    return [value for value in refs if value in mandatory]


def schedule(state, records, probe):
    config, base, work = state['config'], state['base'], state['work']
    policy_required = state['policy']['required_specifications']
    numeric = set(state['policy'].get('numeric_specifications', []))
    medium = yield from ask(state, 'medium',
                            'この作品を何で作るかを1つ選んでください。physical=物として作る、digital=画面・ファイルとして作る、hybrid=両方。',
                            {key: work[key] for key in ('work_title', 'proposition', 'components', 'material', 'mechanism')},
                            optional=('mechanism',), choices=config['media'])
    records['medium'] = medium
    location = yield from ask(state, 'environment.location',
                              'この作品を制作・設置する場所と環境（屋内か屋外か、広さ、壁や床、電源など）を具体的に',
                              {key: work[key] for key in ('work_title', 'components', 'mechanism', 'venue_dependency')},
                              optional=('mechanism',), checks=['one_sentence'], min_chars=10, max_chars=200)
    records['environment'] = {'location': location}
    specs = {}
    for name in policy_required[medium]:
        label = config['specification_labels'].get(name, name)
        value = yield from ask(state, f'spec.{name}', f'この作品の仕様のうち「{label}」を、制作者がそのまま使える具体的な値として',
                               {'work_title': work['work_title'], 'components': work['components'], 'material': work['material'],
                                'mechanism': work['mechanism'], 'requirements': work['requirements'],
                                'environment': location, 'accepted_specifications': dict(specs)},
                               optional=('requirements', 'mechanism', 'accepted_specifications'),
                               checks=['one_sentence', *(['has_number'] if name in numeric else [])], min_chars=5, max_chars=240)
        specs[name] = value
    records['specifications'] = specs
    previous = []
    for task in base['tasks']:
        tid = task['id']
        task_inputs = {'work_title': work['work_title'], 'task': task['title'], 'effect_type': task['effect_type'],
                       'task_done_when': task['acceptance_condition'], 'specifications': specs,
                       'previous_steps': previous[-3:]}
        instruction = yield from ask(state, f'step.{tid}.instruction',
                                     'この作業（task）の手順を、何を・どれだけ・どう行うかが分かる具体的な文として',
                                     task_inputs, optional=('previous_steps', 'specifications'),
                                     checks=['one_sentence'], min_chars=20, max_chars=240)
        if instruction is None:
            continue
        completion = yield from ask(state, f'step.{tid}.completion',
                                    'この作業が終わったことを確かめる方法（何を見て・測って、どうなっていれば完了か）を',
                                    {'task': task['title'], 'instruction': instruction, 'task_done_when': task['acceptance_condition']},
                                    checks=['one_sentence'], min_chars=15, max_chars=200)
        if completion is None:
            continue
        step = {'task': task, 'instruction': instruction, 'completion': completion}
        if task['effect_type'] == 'PHYSICAL_EXTERNAL':
            physical = {'task': task['title'], 'instruction': instruction, 'materials_spec': specs.get('materials', '')}
            step['material'] = yield from ask(state, f'step.{tid}.material',
                                              'この作業で使う主な材料を1つ、種類と大きさが分かる具体名で返してください。',
                                              physical, optional=('materials_spec',), min_chars=2, max_chars=80)
            units = state['units']
            step['quantity'] = yield from ask(state, f'step.{tid}.material-quantity',
                                              'その材料（material）の数量を「数 単位」の形で返してください。単位は units のどれか1つ'
                                              '（枚は sheet、個は item、組は set）。例: 12 sheet',
                                              {**physical, 'material': step['material'], 'units': units},
                                              optional=('materials_spec',), checks=['quantity:' + ','.join(units)], max_chars=24)
            step['tool'] = yield from ask(state, f'step.{tid}.tool',
                                          'この作業で使う主な道具・機材を1つ、具体名で返してください。',
                                          {**physical, 'material': step['material']}, optional=('materials_spec',),
                                          checks=['not_same_as:material'], min_chars=2, max_chars=80)
        previous.append(instruction)
        records['steps'].append(step)
    # Program: the plan this method produces decides which gaps need a resolution check.
    method = assemble(state, records, uncertainties=[])
    plan = probe(method)
    records['probe_gaps'] = [g for g in plan['gaps'] if g.get('rule') not in config['approval_gap_rules']]
    state['gap_policies'] = {g['id']: bool(g.get('blocking')) for g in records['probe_gaps']}
    uncertainties = []
    for gap in records['probe_gaps']:
        gid = gap['id']
        gap_inputs = {'work_title': work['work_title'], 'gap': gap['statement'], 'impact': gap.get('impact', ''),
                      'owner': gap.get('owner', 'production'), 'blocking': bool(gap.get('blocking'))}
        check = yield from ask(state, f'gap.{gid}.method',
                               'この未確定事項（gap）を確かめる・解消する方法（誰が・何を調べ・何をするか）を具体的に',
                               gap_inputs, checks=['one_sentence'], min_chars=15, max_chars=240)
        if check is None:
            continue
        condition = yield from ask(state, f'gap.{gid}.condition',
                                   'この未確定事項が解消したとみなす条件（何がどうなっていればよいか）を',
                                   {**gap_inputs, 'method': check}, checks=['one_sentence', 'not_same_as:method'],
                                   min_chars=10, max_chars=200)
        if condition is None:
            continue
        owner = gap.get('owner') or gap.get('resolution_owner') or 'production'
        uncertainties.append({'topic': gid, 'check_method': check,
                              'actor': config['gap_actors'].get(owner, config['gap_actors']['production']),
                              'condition': condition})
    records['uncertainties'] = uncertainties


def assemble(state, records, *, uncertainties):
    """The production-method/v1 document from accepted values; wiring is program work."""
    config, base, work = state['config'], state['base'], state['work']
    mandatory = set(base['mandatory_requirement_ids'])
    specs = records['specifications']
    names = list(specs)
    materials, resources, steps = [], [], []
    tool_ids = {}
    next_material = len(base['material_ids']) + 1
    next_resource = len(base['resource_ids']) + 1
    tests_by_requirement = {}
    for test in work['acceptance_tests']:
        tests_by_requirement.setdefault(test['target_requirement'], []).append(test['id'])
    for step in records['steps']:
        task = step['task']
        material_ids, resource_ids = list(task['required_material_ids']), list(task['required_resource_ids'])
        if step.get('material'):
            mid = f'MT{next_material:03}'
            next_material += 1
            materials.append({'id': mid, 'name': step['material'], 'specification': specs.get('materials', step['material']),
                              'quantity': quantity(step['quantity']), 'task_ids': [task['id']]})
            material_ids.append(mid)
        if step.get('tool'):
            key = ' '.join(step['tool'].split())
            if key not in tool_ids:
                tool_ids[key] = f'RS{next_resource:03}'
                next_resource += 1
                resources.append({'id': tool_ids[key], 'type': 'EQUIPMENT', 'capability': step['tool'],
                                  'quantity': {'value': '1', 'unit': 'set'}, 'task_ids': []})
            record = next(r for r in resources if r['id'] == tool_ids[key])
            record['task_ids'].append(task['id'])
            resource_ids.append(tool_ids[key])
        conditions = [f'{dep} の完了確認が済んでいる。' for dep in task['depends_on']]
        if task['effect_type'] not in LOCAL_EFFECTS:
            conditions.append('この作業の承認が承認台帳に記録されている（承認前は実施しない）。')
        if not conditions:
            conditions.append('受理済みの handoff と制作プランが手元にある。')
        steps.append({'task_id': task['id'], 'instruction': step['instruction'],
                      'actor': config['actors'].get(task['effect_type'], 'production-agent'),
                      'conditions': conditions, 'material_ids': material_ids, 'resource_ids': resource_ids,
                      'requirement_ids': requirement_ids(task, mandatory), 'specification_names': names,
                      'completion_check': step['completion'], 'evidence_status': 'NOT_RUN'})
    users = {}
    for step in steps:
        for name in step['specification_names']:
            users.setdefault(name, []).append(step['task_id'])
    tests = sorted({t for ids in tests_by_requirement.values() for t in ids})
    specifications = [{
        'name': name, 'value': value, 'status': 'PROPOSED',
        'reason': f"段階 D の要素 spec.{name} への回答。作品案 {work['hypothesis_id']} の構成要素・素材・要件から提案した値で、実測ではない。",
        'verification': {
            'check_method': f"完成した作品または試作で{config['specification_labels'].get(name, name)}を確かめ、提案値「{value}」と一致するかを記録する。",
            'actor': 'production-agent',
            'condition': f"この仕様を使う作業（{', '.join(users.get(name, [])) or 'なし'}）が終わり、受け入れテスト（{', '.join(tests) or 'なし'}）を行う前。"
            if users.get(name) and tests else '仕様を使う作業が終わった後。'}}
        for name, value in specs.items()]
    return {'contract_version': 'production-method/v1', 'handoff_sha256': base['handoff_sha256'],
            'medium': records['medium'], 'environment': dict(records['environment']),
            'specifications': specifications, 'steps': steps, 'knowledge_uses': [],
            'uncertainties': list(uncertainties), 'materials': materials, 'resources': resources}


def advance(state, probe):
    """Find the next unanswered request; when none remains, return the assembled method."""
    records = {'steps': [], 'specifications': {}, 'environment': {}, 'medium': None, 'uncertainties': []}
    pending = None
    for request in schedule(state, records, probe):
        if request['element_id'] not in state['answers'] and request['element_id'] not in state['abandoned']:
            pending = request
            break
    state['pending'] = pending
    if pending is not None:
        return None
    return assemble(state, records, uncertainties=records['uncertainties'])


def element_for_finding(finding: str, state) -> str | None:
    """Map an actionability finding back to the element that produced the value, if any."""
    rule, _, detail = finding.partition(': ')
    if rule == 'PLACEHOLDER_VALUE':
        where, _, field = detail.partition('/')
        kind, _, key = where.partition(':')
        if where.startswith('environment'):
            return 'environment.location'
        if kind == 'specification':
            return f'spec.{key}'
        if kind == 'step':
            return f"step.{key}.{'completion' if field == 'completion_check' else 'instruction'}"
        if kind == 'uncertainty':
            return f"gap.{key}.{'condition' if field == 'condition' else 'method'}"
        if kind in ('material', 'resource'):
            owner = state.get('method_owners', {}).get(key)
            return f"step.{owner}.{'material' if kind == 'material' else 'tool'}" if owner else None
    if rule in ('SPECIFICATION_NOT_CONCRETE', 'SPECIFICATION_UNRESOLVED'):
        return f'spec.{detail}'
    if rule == 'GAP_RESOLUTION_REQUIRED':
        return f'gap.{detail}.method'
    if rule == 'PROCEDURE_REQUIRED' and detail:
        return f'step.{detail}.instruction'
    return None
