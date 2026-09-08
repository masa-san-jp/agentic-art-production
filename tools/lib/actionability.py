"""Content qualification of proposed production methods; grants no execution authority."""
from pathlib import Path
from .canonical import canonical_sha256
from .config import load_config
from .schema import load_schema, validate_instance


def assess(plan, repository):
    method = plan.get('production_method')
    result = {'contract_version': 'plan-actionability/v1', 'method_sha256': None,
              'plan_status': 'INCOMPLETE', 'findings': [], 'first_task_id': None,
              'external_effects_authorized': False}
    failures = result['findings']
    if method is None:
        failures.append('METHOD_REQUIRED: no proposed production method supplied')
        return result
    result['method_sha256'] = canonical_sha256(method)
    schema_path = repository / 'schemas/production-method.schema.json'
    errors = validate_instance(method, load_schema(schema_path), schema_path=schema_path)
    if errors:
        failures.extend('METHOD_SCHEMA: ' + e.reason for e in errors)
        return result
    if method['handoff_sha256'] != plan['handoff_ref']['content_sha256']:
        failures.append('HANDOFF_MISMATCH: method belongs to another accepted handoff')
    specs = {s['name']: s for s in method['specifications']}
    if len(specs) != len(method['specifications']):
        failures.append('DUPLICATE_SPECIFICATION')
    required = load_config(repository, 'plan-actionability.yaml')['required_specifications'][method['medium']]
    for name in required:
        if name not in specs:
            failures.append('SPECIFICATION_REQUIRED: ' + name)
        elif specs[name]['status'] == 'UNKNOWN':
            failures.append('SPECIFICATION_UNRESOLVED: ' + name)
    tasks = {t['id']: t for t in plan['tasks']}
    materials = {m['id'] for m in plan['materials']}
    resources = {r['id'] for r in plan['resources']}
    requirements = set(plan['mandatory_requirement_ids'])
    covered = set()
    seen = set()
    for step in method['steps']:
        tid = step['task_id']
        if tid in seen:
            failures.append('DUPLICATE_STEP: ' + tid)
        seen.add(tid)
        task = tasks.get(tid)
        if not task:
            failures.append('TASK_REFERENCE: ' + tid)
            continue
        if not step['conditions']:
            failures.append('START_CONDITIONS_REQUIRED: ' + tid)
        for field, domain in [('material_ids', materials), ('resource_ids', resources), ('requirement_ids', requirements), ('specification_names', set(specs))]:
            if set(step[field]) - domain:
                failures.append('STEP_REFERENCE: ' + tid + '/' + field)
        if not set(task.get('required_material_ids', [])) <= set(step['material_ids']) or not set(task.get('required_resource_ids', [])) <= set(step['resource_ids']):
            failures.append('TASK_INPUT_CONTRADICTION: ' + tid)
        allowed_requirements = set(task.get('requirement_ids', task.get('trace_refs', []))) & requirements
        if not set(step['requirement_ids']) <= allowed_requirements:
            failures.append('REQUIREMENT_CONTRADICTION: ' + tid)
        if not step['requirement_ids'] or not step['specification_names']:
            failures.append('STEP_GROUNDING_REQUIRED: ' + tid)
        if any(specs[n]['status'] != 'PROPOSED' for n in step['specification_names'] if n in specs):
            failures.append('STEP_SPECIFICATION_CONTRADICTION: ' + tid)
        covered.update(step['requirement_ids'])
    if requirements - covered:
        failures.append('PROCEDURE_COVERAGE: ' + ','.join(sorted(requirements - covered)))
    if not method['steps']:
        failures.append('PROCEDURE_REQUIRED')
    # Every external production task needs an explicit method, including later steps.
    for tid, task in tasks.items():
        if task.get('effect_type') not in {'READ_ONLY', 'REPOSITORY_WRITE'} and tid not in seen:
            failures.append('PROCEDURE_REQUIRED: ' + tid)
    roots = [s['task_id'] for s in method['steps'] if not set(tasks.get(s['task_id'], {}).get('depends_on', [])) & seen]
    if roots:
        result['first_task_id'] = roots[0]
    else:
        failures.append('FIRST_ACTION_REQUIRED')
    topics = {u['topic'] for u in method['uncertainties']}
    for gap in plan['gaps']:
        # Human execution approvals stay in their own register, never silently waived.
        if gap.get('blocking') and gap.get('rule') != 'PLANNING_EXTERNAL_READY':
            failures.append('NATIVE_BLOCKING_GAP: ' + gap['id'])
        if gap.get('id') not in topics and gap.get('rule') != 'PLANNING_EXTERNAL_READY':
            failures.append('GAP_RESOLUTION_REQUIRED: ' + gap['id'])
    for use in method['knowledge_uses']:
        if use['target_id'] not in materials | resources | set(tasks) | {'budget', 'schedule'}:
            failures.append('KNOWLEDGE_TARGET: ' + use['target_id'])
        if any(method['environment'].get(k) != v for k, v in use['source_conditions'].items()):
            failures.append('KNOWLEDGE_ENVIRONMENT_MISMATCH: ' + use['record_id'])
    if plan['coverage_report']['coverage_percent'] != 100:
        failures.append('NATIVE_COVERAGE_INCOMPLETE')
    if plan['visual_package']['status'] != 'READY':
        failures.append('VISUAL_PACKAGE_INCOMPLETE')
    if any(r['access_status'] != 'AVAILABLE' for r in plan['reference_access']):
        failures.append('REFERENCE_UNAVAILABLE')
    if not failures:
        result['plan_status'] = 'PLAN_READY'
    return result


def render(plan):
    assessment = plan.get('actionability')
    if assessment is None:
        return ''
    lines = ['','## 制作を始めるための手順', '', '内容検証: ' + assessment['plan_status'],
             '以下は提案です。試験・購入・設営は未実施で、外部操作の承認を含みません。', '']
    method = plan.get('production_method')
    if method:
        lines += ['媒体: ' + method['medium'], '制作環境: ' + str(method['environment']), '']
        for spec in method['specifications']:
            lines += [f"- {spec['name']}: {spec['value']} ({spec['status']}) — {spec['reason']}",
                      f"  確認: {spec['verification']['check_method']} / 担当: {spec['verification']['actor']} / 条件: {spec['verification']['condition']}"]
        for step in method['steps']:
            label = '最初の制作作業' if step['task_id'] == assessment['first_task_id'] else '制作作業'
            lines += ['', f"### {label}: {step['task_id']}", step['instruction'],
                      f"担当: {step['actor']} / 開始条件: {'; '.join(step['conditions'])}",
                      f"必要な材料: {', '.join(step['material_ids']) or 'なし'} / 資源: {', '.join(step['resource_ids']) or 'なし'}",
                      f"要件: {', '.join(step['requirement_ids'])} / 仕様: {', '.join(step['specification_names'])}",
                      '完了確認（未実施）: ' + step['completion_check']]
        lines += ['', '### 未確定事項の確認']
        lines += [f"- {u['topic']}: {u['check_method']} / 担当: {u['actor']} / 成立条件: {u['condition']}" for u in method['uncertainties']]
        lines += ['', '### 再利用する知識の条件']
        lines += [f"- {u['target_id']}: {u['use']} / 記録: {u['record_id']} / source条件: {u['source_conditions']} / code: {u['code_commit']} / knowledge: {u['knowledge_commit']}" for u in method['knowledge_uses']]
    lines += ['', *['- 未充足: ' + f for f in assessment['findings']], '']
    return '\n'.join(lines)
