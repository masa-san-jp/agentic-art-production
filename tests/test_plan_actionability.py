"""Observable production content qualification, including damaged real output files."""
import copy
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from tools import build_plan, plan_actionability
from tools.new_production import main as new_production
from tools.lib.actionability import assess
from tools.lib.yaml_io import load_yaml, dump_yaml
from tools.lib.canonical import canonical_sha256
from tools.lib.planning import validate_plan_document
ROOT = Path(__file__).resolve().parents[1]


class PlanActionabilityTests(unittest.TestCase):
    def make(self, fixture='task-matrix', medium='physical'):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        output = Path(temp.name)
        import shutil
        from tests.test_bootstrap import BootstrapContractTests
        bundle = output / 'bundle'
        shutil.copytree(ROOT/'tests/fixtures/handoff'/fixture, bundle)
        if fixture == 'task-matrix':
            # Synthetic positive variant supplies the missing visual category;
            # the original incomplete fixture is preserved unchanged.
            path = bundle/'artifacts/source-ref-index.yaml'
            refs = load_yaml(path)
            refs['references'][1]['reference_categories'].append('VISUAL')
            dump_yaml(refs, path)
            BootstrapContractTests()._refresh_bundle_manifest(bundle)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, new_production(['method', '--handoff', str(bundle), '--output-root', str(output)]))
        project = output/'production/method'
        plan = build_plan._build_plan(project)
        proposed = {'dimensions':'300 × 300 mm test panel', 'materials':'One synthetic test sheet from the material register',
                    'assembly':'Mark a 10 mm grid, align repeated elements to the marked intersections',
                    'installation':'Lay flat on a stable indoor table for comparison',
                    'format':'PNG, lossless RGB image', 'resolution':'1200 × 1200 pixels',
                    'duration':'Static image; no playback duration', 'software':'Offline SVG editor with PNG export',
                    'delivery':'Save a local PNG preview for visual review'}
        names = ['dimensions','materials','assembly','installation'] if medium == 'physical' else ['format','resolution','duration','software','delivery']
        check = {'check_method':'Compare the proposed fixture against the acceptance test before approving execution', 'actor':'synthetic operator', 'condition':'A safe local fixture and its references are available'}
        method = {'contract_version':'production-method/v1','handoff_sha256':plan['handoff_ref']['content_sha256'],
                  'medium':medium,'environment':{'location':'synthetic indoor bench'},
                  'specifications':[{'name':n,'value':proposed[n],'status':'PROPOSED','reason':'Synthetic proposal for this test; not an observed result','verification':check} for n in names],
                  'steps':[], 'knowledge_uses':[],
                  'uncertainties':[dict(topic=g['id'], **check) for g in plan['gaps']]}
        for task in plan['tasks']:
            if task['effect_type'] not in {'READ_ONLY','REPOSITORY_WRITE'}:
                method['steps'].append({'task_id':task['id'], 'instruction':('Mark the test sheet with a 10 mm grid and align repeated elements at the intersections.' if medium == 'physical' else 'Create a 1200 pixel square SVG canvas, place repeated elements at a 40 pixel pitch, and export a local PNG preview.'),
                    'actor':'synthetic operator','conditions':['Obtain the separate task approval and verify the safe workspace'],
                    'material_ids':task.get('required_material_ids',[]),'resource_ids':task.get('required_resource_ids',[]),
                    'requirement_ids':['RQ001'],'specification_names':names,'completion_check':'Compare repeated element spacing with AT001; record discrepancies without claiming a pass.', 'evidence_status':'NOT_RUN'})
        dump_yaml(method, project/'02_specification/production-method.yaml')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, build_plan.main(['--project-root',str(project)]))
        return project, load_yaml(project/'03_plan/production-plan.yaml')

    def test_distinct_handoffs_media_and_first_action_rubric(self):
        for fixture, medium, phrase in [('task-matrix','physical','10 mm grid'),('minimal','digital','1200 pixel square')]:
            with self.subTest(medium=medium):
                project, plan = self.make(fixture, medium)
                result = plan_actionability.verify(project)
                self.assertEqual('PLAN_READY', result['plan_status'], result)
                self.assertEqual(100, plan['coverage_report']['coverage_percent'])
                body = (project/'03_plan/production-plan.md').read_text()
                for required in [phrase, '最初の制作作業', '必要な材料:', '資源:', '担当:', '開始条件:', '完了確認（未実施）']:
                    self.assertIn(required, body)
                self.assertEqual('PLANNING', plan['state'])
                self.assertFalse(result['external_effects_authorized'])
                self.assertTrue(all(s['evidence_status']=='NOT_RUN' for s in plan['production_method']['steps']))

    def test_empty_unknown_and_nonapplicable_specification(self):
        _, plan = self.make()
        for field, value, expected in [('value','', 'METHOD_SCHEMA'),('status','UNKNOWN','SPECIFICATION_UNRESOLVED')]:
            changed = copy.deepcopy(plan); changed['production_method']['specifications'][0][field]=value
            self.assertTrue(any(expected in f for f in assess(changed,ROOT)['findings']))
        changed=copy.deepcopy(plan); changed['production_method']['specifications']=[]
        self.assertIn('INCOMPLETE',assess(changed,ROOT)['plan_status'])
        changed=copy.deepcopy(plan); changed['production_method']['specifications'][0]['status']='NOT_APPLICABLE'
        self.assertTrue(any('STEP_SPECIFICATION_CONTRADICTION' in f for f in assess(changed,ROOT)['findings']))

    def test_step_requirement_material_and_missing_procedure(self):
        _, plan=self.make()
        for mutate, expected in [(lambda m:m['steps'][0].update(requirement_ids=['RQ999']), 'STEP_REFERENCE'),
                                  (lambda m:m['steps'][0].update(material_ids=[]),'TASK_INPUT_CONTRADICTION'),
                                  (lambda m:m.update(steps=[]),'PROCEDURE_REQUIRED'),
                                  (lambda m:m['steps'][0].update(conditions=[]),'START_CONDITIONS_REQUIRED')]:
            changed=copy.deepcopy(plan); mutate(changed['production_method'])
            self.assertTrue(any(expected in f for f in assess(changed,ROOT)['findings']), expected)

    def test_unsupported_actuals_and_unresolved_gaps(self):
        _, plan=self.make()
        changed=copy.deepcopy(plan); changed['production_method']['steps'][0]['evidence_status']='PASSED'
        self.assertEqual('INCOMPLETE', assess(changed,ROOT)['plan_status'])
        changed=copy.deepcopy(plan); changed['production_method']['uncertainties']=[]
        self.assertTrue(any('GAP_RESOLUTION_REQUIRED' in f for f in assess(changed,ROOT)['findings']))

    def test_knowledge_conditions_and_independent_commits(self):
        _, plan=self.make()
        use={'target_id':plan['tasks'][0]['id'],'code_commit':'a'*40,'knowledge_commit':'b'*40,'record_id':'synthetic-procedure/1',
             'source_conditions':{'location':'synthetic indoor bench'},'use':'Use the earlier test spacing as a proposed starting point, not proof of success'}
        plan['production_method']['knowledge_uses']=[use]
        self.assertEqual('PLAN_READY',assess(plan,ROOT)['plan_status'])
        use['source_conditions']['location']='outdoor rain'
        self.assertTrue(any('KNOWLEDGE_ENVIRONMENT_MISMATCH' in f for f in assess(plan,ROOT)['findings']))

    def test_internal_references_need_gap_checks_not_fabricated_external_urls(self):
        _, plan = self.make()
        plan['reference_access'].append({'source_ref_id':'DC-INTERNAL', 'kind':'decision', 'reference_categories':['OTHER'], 'summary':'Internal canonical decision', 'access_url':None, 'access_status':'MISSING', 'record_hash':'sha256:'+'a'*64})
        plan['gaps'].append({'id':'PG-INTERNAL', 'statement':'Internal record has no external URL', 'blocking':False})
        check = dict(plan['production_method']['uncertainties'][0])
        check['topic'] = 'PG-INTERNAL'
        plan['production_method']['uncertainties'].append(check)
        self.assertEqual('PLAN_READY', assess(plan, ROOT)['plan_status'])
        plan['production_method']['uncertainties'].pop()
        self.assertIn('GAP_RESOLUTION_REQUIRED: PG-INTERNAL', assess(plan, ROOT)['findings'])
        plan['production_method']['uncertainties'].append(check)
        for reference in plan['reference_access']:
            reference['reference_categories'] = [c for c in reference['reference_categories'] if c != 'METHOD']
        self.assertIn('REFERENCE_UNAVAILABLE: METHOD', assess(plan, ROOT)['findings'])

    def test_visual_actual_file_and_reference_damage(self):
        project, plan=self.make()
        from tools.public_plan_attestation import linked_assets
        asset=project/next(iter(linked_assets((project/'03_plan/production-plan.md').read_text())))
        asset.unlink()
        self.assertEqual('INCOMPLETE',plan_actionability.verify(project)['plan_status'])
        plan['reference_access'][0]['access_url']='file:///private/data'
        rules={f.rule for f in validate_plan_document(plan,repository=ROOT,plan_path=project/'03_plan/production-plan.yaml')}
        self.assertIn('PLANNING_REFERENCE_URL',rules)

    def test_canonical_body_method_hash_and_forged_ready(self):
        project, plan=self.make()
        body=project/'03_plan/production-plan.md'; body.write_text(body.read_text()+'altered')
        self.assertIn('CANONICAL_RENDERER_MISMATCH',plan_actionability.verify(project)['findings'])
        changed=copy.deepcopy(plan); changed['production_method']['steps']=[]
        changed['integrity']['content_sha256']=canonical_sha256({k:v for k,v in changed.items() if k!='integrity'})
        self.assertIn('PLAN_ACTIONABILITY',{f.rule for f in validate_plan_document(changed,repository=ROOT,plan_path=body)})
        self.assertEqual('INCOMPLETE',assess({k:v for k,v in plan.items() if k!='production_method'},ROOT)['plan_status'])


class ActionabilityAttestationTests(unittest.TestCase):
    make = PlanActionabilityTests.make
    def test_public_attestation_binds_actionable_body(self):
        from tools.public_plan_attestation import build_attestation, verify_attestation, linked_assets
        from tools.lib.canonical import sha256_bytes
        import subprocess
        project, plan = self.make()
        body=(project/'03_plan/production-plan.md').read_bytes()
        review={'contract_version':'public-plan-review/v1','policy_version':'public-plan-policy/v1',
                'aggregate_sha256':sha256_bytes((project/'03_plan/production-plan.yaml').read_bytes()),
                'body_sha256':sha256_bytes(body),'content_safety':'PASSED','rights':'PASSED','consent':'PASSED',
                'consent_ref':'consent/synthetic-fixture-only','assets':[
                    {'path':path,'sha256':sha256_bytes((project/path).read_bytes()),'byte_length':(project/path).stat().st_size,
                     'media_type':'image/svg+xml','rights_status':'PUBLIC_CLEARED','rights_ref':'rights/synthetic-fixture-only'}
                    for path in sorted(linked_assets(body.decode()))]}
        code=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        result=build_attestation(project,review,producer_commit=code,generated_at='2026-09-08T00:00:00Z')
        verify_attestation(project,result)
        self.assertFalse(result['external_effects_authorized'])
        (project/'03_plan/production-plan.md').write_bytes(body+b' ')
        with self.assertRaisesRegex(ValueError,'CANONICAL_RENDER_MISMATCH'):
            build_attestation(project,review,producer_commit=code,generated_at='2026-09-08T00:00:00Z')


if __name__ == "__main__": unittest.main()
