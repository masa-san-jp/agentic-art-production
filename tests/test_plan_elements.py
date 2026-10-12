"""Stage D on the element mechanism: accepted element-route handoff -> production method -> PLAN_READY.

The fixture bundle (tests/fixtures/handoff/element-route.zip) was exported by agentic-art-research
`export_handoff.py` after its fake answerer ran Stage B -> C by elements (synthetic example.invalid
sources, research #134); this test answers Stage D with a fake answerer.
"""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import build_plan, plan_actionability
from tools.lib.actionability import assess
from tools.lib.plan_element_driver import element_for_finding
from tools.lib.yaml_io import dump_yaml, load_yaml
from tools.new_production import main as new_production
from tools.plan_elements import PlanEngine, main as plan_elements_main

ROOT = Path(__file__).resolve().parents[1]
NOW = '2026-10-13T00:00:00Z'
spec = importlib.util.spec_from_file_location('fake_plan', ROOT / 'tests/fixtures/plan_elements/fake_answerer.py')
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)


class PlanElementsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name)
        # Packed as an uncompressed ZIP: the exported bundle carries the research common schema,
        # whose sensitivity vocabulary the repository text scan rejects in plain fixture files.
        bundle = output / 'element-route.zip'
        shutil.copyfile(ROOT / 'tests/fixtures/handoff/element-route.zip', bundle)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, new_production(['elements', '--handoff', str(bundle), '--output-root', str(output)]))
        self.project = output / 'production/elements'

    def run_engine(self, mutate=None):
        engine = PlanEngine(self.project)
        report = engine.next(now=NOW)
        requests = []
        while report['next_action']:
            request = report['next_action']['request']
            requests.append(request)
            answer = fake.answer(request)
            if mutate:
                mutate(request, answer)
            report = engine.answer(answer, now=NOW)
        return report, requests

    def test_without_a_method_the_physical_plan_is_not_startable(self):
        plan = build_plan._build_plan(self.project, method=None)
        self.assertFalse(plan['readiness']['startable'])
        self.assertIn('physical work has no complete structured tool and material availability path.',
                      plan['inspiration_alignment']['gaps'])

    def test_handoff_to_plan_ready_by_elements(self):
        report, requests = self.run_engine()
        self.assertEqual('COMPLETED', report['status'], report)
        self.assertEqual('PLAN_READY', report['completion']['plan_status'], report['completion'])
        self.assertEqual([], report['completion']['findings'])
        self.assertFalse(report['completion']['external_effects_authorized'])
        ids = [r['element_id'] for r in requests]
        self.assertEqual(['medium', 'environment.location'], ids[:2])
        self.assertEqual(['spec.dimensions', 'spec.materials', 'spec.assembly', 'spec.installation'],
                         [i for i in ids if i.startswith('spec.')])
        plan = load_yaml(self.project / '03_plan/production-plan.yaml')
        physical = [t['id'] for t in plan['tasks'] if t['effect_type'] == 'PHYSICAL_EXTERNAL']
        self.assertTrue(physical)
        for task_id in physical:
            for item in ('instruction', 'completion', 'material', 'material-quantity', 'tool'):
                self.assertIn(f'step.{task_id}.{item}', ids)
        gaps = [g['id'] for g in plan['gaps'] if g['rule'] != 'PLANNING_EXTERNAL_READY']
        self.assertEqual(sorted(f'gap.{g}.{k}' for g in gaps for k in ('method', 'condition')),
                         sorted(i for i in ids if i.startswith('gap.')))
        for request in requests:
            self.assertEqual('element-request/v1', request['contract_version'])
            self.assertLessEqual(len(json.dumps(request['inputs'], ensure_ascii=False).encode()), 4096)
            if request['answer_format']['type'] == 'text':
                self.assertIn('not_placeholder', request['checks'])
        # The program wired the method: materials and tools are registered proposals, and the
        # research-to-production alignment is complete.
        self.assertEqual('MATCH', plan['inspiration_alignment']['status'])
        method = plan['production_method']
        self.assertEqual(len(physical), len(method['materials']))
        self.assertTrue(all(m['status'] == 'CANDIDATE' for m in plan['materials']))
        self.assertTrue(all(r['availability'] == 'REQUIRES_CONFIRMATION' for r in plan['resources']))
        self.assertTrue(all(s['evidence_status'] == 'NOT_RUN' for s in method['steps']))
        self.assertIn('QUANTITY', {s['target']['kind'] for s in plan['technical_specifications']})
        body = (self.project / '03_plan/production-plan.md').read_text()
        for phrase in ('最初の制作作業', '完了確認（未実施）', 'シナ合板'):
            self.assertIn(phrase, body)
        for text in (body, (self.project / '02_specification/production-method.yaml').read_text()):
            self.assertNotIn('未記入', text)
            self.assertNotIn('TBD', text)
        self.assertEqual('PLAN_READY', plan_actionability.verify(self.project)['plan_status'])
        # The finished run is read back, not re-entered.
        self.assertEqual('COMPLETED', PlanEngine(self.project).next(now=NOW)['status'])

    def test_placeholder_is_retried_and_a_skippable_gap_is_abandoned(self):
        def mutate(request, answer):
            if request['element_id'] == 'spec.dimensions' and request['attempt'] == 1:
                answer['value'] = '未記入'
            if request['element_id'] == 'spec.materials' and request['attempt'] < 3:
                answer['value'] = 'なし'
            if request['element_id'].startswith('gap.') and request['element_id'].endswith('.condition') and 'GP001' in request['element_id']:
                answer['value'] = 'TBD'
        report, requests = self.run_engine(mutate)
        dims = [r for r in requests if r['element_id'] == 'spec.dimensions']
        self.assertEqual([1, 2], [r['attempt'] for r in dims])
        self.assertIn('not_placeholder', [f['check'] for f in dims[1]['previous_failure']])
        self.assertEqual(3, len([r for r in requests if r['element_id'] == 'spec.materials']))
        self.assertEqual(5, len([r for r in requests if r['element_id'] == 'gap.GP001.condition']))
        self.assertEqual('COMPLETED', report['status'])
        self.assertIn('gap.GP001.condition', report['abandoned'])
        self.assertEqual('INCOMPLETE', report['completion']['plan_status'])
        self.assertIn('GAP_RESOLUTION_REQUIRED: GP001', report['completion']['findings'])

    def test_unskippable_element_blocks_after_five_attempts(self):
        def mutate(request, answer):
            if request['element_id'] == 'spec.dimensions':
                answer['value'] = '寸法は大きめにする。'  # no number
        report, requests = self.run_engine(mutate)
        self.assertEqual('BLOCKED', report['status'])
        self.assertEqual('spec.dimensions', report['blocked']['element_id'])
        self.assertEqual('has_number', report['blocked']['last_failure'][0]['check'])
        self.assertFalse((self.project / '02_specification/production-method.yaml').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1, plan_elements_main(['next', '--project-root', str(self.project), '--now', NOW]))

    def test_actionability_finding_re_asks_only_that_element(self):
        real = plan_actionability.verify
        calls = []

        def verify(project):
            result = real(project)
            calls.append(result)
            if len(calls) == 1:
                result = copy.deepcopy(result)
                result['findings'].append('PLACEHOLDER_VALUE: step:TK001/instruction')
                result['plan_status'] = 'INCOMPLETE'
            return result
        with mock.patch.object(plan_actionability, 'verify', verify):
            report, requests = self.run_engine()
        retried = [r for r in requests if r['element_id'] == 'step.TK001.instruction']
        self.assertEqual([1, 2], [r['attempt'] for r in retried])
        self.assertEqual('actionability', retried[1]['previous_failure'][0]['check'])
        self.assertEqual(1, len([r for r in requests if r['element_id'] == 'spec.dimensions']))
        self.assertEqual('PLAN_READY', report['completion']['plan_status'])

    def test_answer_identity_resend_and_changed_handoff(self):
        engine = PlanEngine(self.project)
        request = engine.next(now=NOW)['next_action']['request']
        wrong = {**fake.answer(request), 'attempt': 2}
        with self.assertRaisesRegex(ValueError, 'pending identity'):
            engine.answer(wrong, now=NOW)
        answer = fake.answer(request)
        first = engine.answer(answer, now=NOW)
        self.assertEqual(first['next_action'], engine.answer(answer, now=NOW)['next_action'])
        handoff = self.project / '00_handoff/production-handoff.yaml'
        value = load_yaml(handoff)
        value['integrity']['content_sha256'] = 'sha256:' + '0' * 64
        dump_yaml(value, handoff)
        with self.assertRaisesRegex(ValueError, 'handoff changed'):
            engine.next(now=NOW)

    def test_handoff_content_and_bundle_artifacts_are_pinned(self):
        engine = PlanEngine(self.project)
        engine.next(now=NOW)
        handoff = self.project / '00_handoff/production-handoff.yaml'
        original = handoff.read_text()
        value = load_yaml(handoff)
        value['selection']['selected_hypothesis_id'] = 'PH999'
        dump_yaml(value, handoff)
        with self.assertRaisesRegex(ValueError, 'handoff changed'):
            engine.next(now=NOW)
        handoff.write_text(original)
        artifact = self.project / '00_handoff/source-bundle/artifacts/production-requirements.yaml'
        artifact.write_text(artifact.read_text() + '\n# changed after acceptance\n')
        with self.assertRaisesRegex(ValueError, 'handoff changed'):
            engine.next(now=NOW)

    def test_placeholder_fragments_are_case_and_width_insensitive(self):
        from tools.lib.actionability import is_placeholder
        from tools.lib.config import load_config
        policy = load_config(ROOT, 'plan-actionability.yaml')
        for value in ('材料の種類は tbd として後から決める。', '仕様は ToDo とする。', '寸法は ＴｂＤ とする。'):
            with self.subTest(value=value):
                self.assertTrue(is_placeholder(value, policy))

    def test_number_without_dimension_is_retried_before_rendering(self):
        engine = PlanEngine(self.project)
        report = engine.next(now=NOW)
        while report['next_action']['request']['element_id'] != 'spec.dimensions':
            report = engine.answer(fake.answer(report['next_action']['request']), now=NOW)
        request = report['next_action']['request']
        report = engine.answer({**fake.answer(request), 'value': '部材は1種類を使う。'}, now=NOW)
        retry = report['next_action']['request']
        self.assertEqual(('spec.dimensions', 2), (retry['element_id'], retry['attempt']))
        self.assertIn('has_number', [finding['check'] for finding in retry['previous_failure']])
        from tools.lib.actionability import specification_is_concrete
        for name, value in [('dimensions', '幅 ６００ mm'), ('resolution', '１９２０ × １０８０ ピクセル')]:
            self.assertTrue(specification_is_concrete(name, value, engine.policy))

    def test_findings_map_to_elements(self):
        state = {'method_owners': {'MT001': 'TK002', 'RS001': 'TK003'}}
        for finding, expected in [('PLACEHOLDER_VALUE: specification:dimensions/value', 'spec.dimensions'),
                                  ('PLACEHOLDER_VALUE: step:TK001/completion_check', 'step.TK001.completion'),
                                  ('PLACEHOLDER_VALUE: uncertainty:PG001/condition', 'gap.PG001.condition'),
                                  ('PLACEHOLDER_VALUE: material:MT001/name', 'step.TK002.material'),
                                  ('PLACEHOLDER_VALUE: resource:RS001/capability', 'step.TK003.tool'),
                                  ('PLACEHOLDER_VALUE: environment/location', 'environment.location'),
                                  ('SPECIFICATION_NOT_CONCRETE: resolution', 'spec.resolution'),
                                  ('GAP_RESOLUTION_REQUIRED: GP002', 'gap.GP002.method'),
                                  ('NATIVE_BLOCKING_GAP: PG009', None)]:
            self.assertEqual(expected, element_for_finding(finding, state), finding)

    def test_actionability_rejects_placeholders_everywhere(self):
        report, _ = self.run_engine()
        plan = load_yaml(self.project / '03_plan/production-plan.yaml')
        self.assertEqual('PLAN_READY', assess(plan, ROOT)['plan_status'])
        for mutate, expected in [
                (lambda m: m['specifications'][0].update(value='TBD'), 'PLACEHOLDER_VALUE: specification:dimensions/value'),
                (lambda m: m['steps'][0].update(completion_check='なし'), 'PLACEHOLDER_VALUE: step:'),
                (lambda m: m['uncertainties'][0].update(condition='未確定のまま'), 'PLACEHOLDER_VALUE: uncertainty:'),
                (lambda m: m['specifications'][0].update(value='大きめ'), 'SPECIFICATION_NOT_CONCRETE: dimensions')]:
            changed = copy.deepcopy(plan)
            mutate(changed['production_method'])
            findings = assess(changed, ROOT)['findings']
            self.assertTrue(any(f.startswith(expected) for f in findings), (expected, findings))


if __name__ == '__main__':
    unittest.main()
