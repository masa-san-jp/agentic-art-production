"""Production source -> Git reload -> separate plan adoption, and negative evidence cases."""
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools import production_memory as memory, build_plan
from tools.lib.result import build_result
from tools.lib.yaml_io import load_yaml, dump_yaml
from tests import test_observation as observation_tests
from tests import test_plan_actionability as actionability_tests

ROOT=Path(__file__).resolve().parents[1]
AT='2026-09-08T00:00:00Z'

class ProductionMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        # Real isolated Git snapshot of the code under test, including pending
        # edits during development; no mocked persistence or source validation.
        code=self.root/'code';shutil.copytree(ROOT,code,ignore=shutil.ignore_patterns('.git','.venv','__pycache__'))
        for args in [('init','-q'),('add','.'),('-c','user.name=Production test','-c','user.email=test@localhost','commit','-qm','Code under test')]:
            subprocess.run(['git','-C',str(code),*args],check=True,capture_output=True)
        scope=patch.object(memory,'ROOT',code);scope.start();self.addCleanup(scope.stop)
        self.store=self.root/'knowledge.git';self.head=memory.init(self.store,creator='creator-a',instance='instance-a',collection='production-a')['target_commit']
        with contextlib.redirect_stdout(io.StringIO()):
            temp,self.project,self.manager=observation_tests.ObservationContractTests()._project()
        self.addCleanup(temp.cleanup)
        self.conditions={'equipment':'fixture bench','skill':'fixture operator','size':'300x300mm','safety':'indoor nonhazardous fixture','currency':'JPY','as_of':'2026-09-08'}
        self.request={'contract_version':'production-knowledge-request/v1','record_id':'spacing-study','revision':1,'phase':'simulated','kind':'failure','task_id':'TK004','observation_ids':['OB001'],'conditions':self.conditions,'interpretation':{'epistemic_status':'inferred','statement':'Proposed cause: the alignment guide moved; test this hypothesis.'},'use_proposal':'Hold the alignment guide fixed before comparing the repeated elements.','created_at':AT,'valid_until':'2026-10-08T00:00:00Z'}

    def source(self,revision=1,status='ACTIVE',statement='Synthetic prototype: the repeated element alignment drifted by 2 mm.'):
        observation=observation_tests.ObservationContractTests._observation(revision=revision,status=status,statement=statement,source_refs=[{'kind':'PROTOTYPE','id':'PP001','revision':1},{'kind':'TASK','id':'TK004','revision':1},{'evidence_id':'EVD001','revision':1}])
        self.manager.record_observation(observation,occurred_at=f'2026-08-12T12:0{revision}:00+09:00',actor_kind='AGENT',actor_id='observation-test',idempotency_key=f'observation-{revision}')
        build_result(self.project,ROOT,result_id=f'PR{revision:03d}',generated_at='2026-08-12T18:00:00+09:00',production_commit='a'*40)

    def put(self,request=None,operation='capture-one',parent=None):
        return memory.ingest(self.store,self.project,request or self.request,creator='creator-a',collection='production-a',operation=operation,run_id='run-one',parent=parent or self.head)

    def get(self,snapshot=None,environment=None,project_id='production/next'):
        return memory.query(self.store,creator='creator-a',collection='production-a',snapshot=snapshot or memory.git(self.store,'rev-parse',memory.REF),project_id=project_id,environment=environment or self.conditions,at=AT)

    def next_plan(self,snapshot,select=True):
        helper=actionability_tests.PlanActionabilityTests()
        with contextlib.redirect_stdout(io.StringIO()):project,plan=helper.make('minimal','digital')
        self.addCleanup(helper.doCleanups)
        path=project/'02_specification/production-method.yaml';method=load_yaml(path);method['environment']=self.conditions;dump_yaml(method,path)
        query={'contract_version':'production-memory-query-request/v1','store':str(self.store),'creator':'creator-a','collection':'production-a','snapshot':snapshot,'at':AT,'environment':self.conditions,'selections':[{'record_id':'spacing-study','target_task_id':method['steps'][0]['task_id'],'reason':'The prior simulated drift changes the proposed alignment procedure.'}] if select else []}
        dump_yaml(query,project/'02_specification/production-memory-query.yaml')
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(0,build_plan.main(['--project-root',str(project)]))
        return project,load_yaml(project/'03_plan/production-plan.yaml')

    def test_orchestration_run_ids_preserve_uppercase_aak_provenance(self):
        request=copy.deepcopy(self.request);request.update(phase='planned',observation_ids=[],record_id='uppercase-run')
        receipt=memory.ingest(self.store,self.project,request,creator='creator-a',collection='production-a',operation='AAK07-AGENT-20260910-R5-agentic-art-production',run_id='AAK07-AGENT-20260910-R5',parent=self.head)
        self.assertEqual('COMMITTED',receipt['status'])
        self.assertEqual(['uppercase-run'],receipt['accepted_ids'])
        self.assertEqual('AAK07-AGENT-20260910-R5',receipt['run_id'])

    def test_native_prototype_observation_git_reload_changes_separate_plan(self):
        self.source();receipt=self.put();self.assertEqual('COMMITTED',receipt['status'])
        (self.store/'production-memory-index.json').unlink()
        found=self.get();candidate=found['candidates'][0]
        self.assertEqual('CROSS_PROJECT_REUSE',candidate['relationship']);self.assertEqual('simulated',candidate['record']['epistemic_status'])
        self.assertEqual('PROTOTYPE',candidate['payload']['observations'][0]['source_refs'][0]['kind'])
        project,plan=self.next_plan(receipt['target_commit'])
        self.assertEqual('ADOPTED',plan['knowledge_reuse']['choices'][0]['status'])
        self.assertIn('Hold the alignment guide fixed',plan['production_method']['steps'][0]['instruction'])
        self.assertEqual('NOT_RUN',plan['production_method']['steps'][0]['evidence_status'])
        self.assertEqual('PLAN_READY',plan['actionability']['plan_status'])
        body=(project/'03_plan/production-plan.md').read_text();self.assertIn('観測事実ではない',body)
        self.assertEqual(receipt['target_commit'],plan['knowledge_reuse']['knowledge_commit'])

    def test_estimates_not_actuals_and_equipment_size_rejected(self):
        request=copy.deepcopy(self.request);request.update(phase='planned',observation_ids=[],kind='estimate')
        receipt=self.put(request);candidate=self.get()['candidates'][0]
        self.assertEqual('proposed',candidate['record']['epistemic_status']);self.assertEqual([],candidate['payload']['observations'])
        for field in ['equipment','size','currency','safety','skill']:
            environment=dict(self.conditions);environment[field]='different'
            self.assertEqual('NOT_APPLICABLE',self.get(environment=environment)['candidates'][0]['status'])
        environment=dict(self.conditions);environment['as_of']='2026-09-09'
        self.assertEqual('REVALIDATE',self.get(environment=environment)['candidates'][0]['status'])
        self.source();request=copy.deepcopy(self.request);request['phase']='observed'
        with self.assertRaisesRegex(ValueError,'SIMULATION_NOT_OBSERVED'):memory.capture(self.project,request)

    def test_duplicate_revision_conflict_and_correction_history(self):
        self.source();first=self.put();again=self.put();self.assertEqual(first['target_commit'],again['target_commit']);self.assertEqual('ALREADY_APPLIED',again['status'])
        request=copy.deepcopy(self.request);request['use_proposal']='Different'
        with self.assertRaisesRegex(ValueError,'CONFLICT'):self.put(request)
        self.source(revision=2,statement='Synthetic correction: drift was 1 mm, not 2 mm.')
        request=copy.deepcopy(self.request);request['revision']=2
        second=self.put(request,operation='correction',parent=first['target_commit'])
        self.assertNotEqual(first['target_commit'],second['target_commit'])
        self.assertEqual(1,self.get(snapshot=first['target_commit'])['candidates'][0]['record']['revision'])
        self.assertEqual(2,self.get()['candidates'][0]['record']['revision'])
        self.assertEqual('SAME_PROJECT_REVISION',self.get(project_id='production/smoke')['candidates'][0]['relationship'])

    def test_no_observations_and_pending_knowledge_do_not_forge_actuals(self):
        request=copy.deepcopy(self.request);request['observation_ids']=[]
        receipt=self.put(request);self.assertEqual('NO_CHANGE',receipt['status']);self.assertEqual('NO_NEW_EVIDENCE',receipt['reason'])
        self.assertEqual(self.head,memory.git(self.store,'rev-parse',memory.REF))
        found=self.get();self.assertEqual([],found['candidates']);self.assertFalse(found['plan_completion_blocked'])
        _,plan=self.next_plan(self.head,select=False)
        self.assertEqual('PLAN_READY',plan['actionability']['plan_status'])
        self.assertEqual('NO_NEW_EVIDENCE',plan['knowledge_reuse']['history_status'])

    def test_retraction_returns_earlier_plan_to_revalidation(self):
        self.source();first=self.put();project,plan=self.next_plan(first['target_commit'])
        use=memory.remember_use(self.store,project,creator='creator-a',collection='production-a',parent=first['target_commit'])
        self.assertEqual('COMMITTED',use['status'])
        self.source(revision=2,status='RETRACTED')
        request=copy.deepcopy(self.request);request['revision']=2
        self.put(request,operation='retract',parent=use['target_commit'])
        candidate=self.get()['candidates'][0];self.assertEqual('REVALIDATE',candidate['status']);self.assertIn(plan['project_id'],candidate['affected_projects'])
        with self.assertRaisesRegex(ValueError,'REUSE_SOURCE_CHANGED'):memory.remember_use(self.store,project,creator='creator-a',collection='production-a',parent=use['target_commit'])

    def test_partial_index_failure_retry_identity_and_source_guards(self):
        self.source()
        with patch.object(memory,'reindex',side_effect=OSError('synthetic index failure')):first=self.put()
        self.assertEqual('INDEX_PENDING',first['status']);self.assertIsNotNone(first['target_commit'])
        retry=self.put();self.assertEqual(first['target_commit'],retry['target_commit']);self.assertEqual('ALREADY_APPLIED',retry['status']);self.assertIsNotNone(retry['index_hash'])
        with self.assertRaisesRegex(ValueError,'OWNER_SCOPE_MISMATCH'):memory.query(self.store,creator='creator-b',collection='production-a',snapshot=retry['target_commit'],project_id='production/next',environment=self.conditions,at=AT)
        request=copy.deepcopy(self.request);request['task_id']='TK999'
        with self.assertRaisesRegex(ValueError,'SOURCE_TASK_MISSING'):memory.capture(self.project,request)
        request=copy.deepcopy(self.request);request['use_proposal']='PRIVATE_RAW'
        with self.assertRaisesRegex(ValueError,'KNOWLEDGE_SECURITY'):memory.capture(self.project,request)

    def test_synthetic_evidence_cannot_be_relabelled_as_observed(self):
        observation=observation_tests.ObservationContractTests._observation(statement='Alignment drift was two millimetres.')
        observation.update(method='ruler measurement',limitations='One bench observation')
        self.manager.record_observation(observation,occurred_at='2026-08-12T12:01:00+09:00',actor_kind='AGENT',actor_id='observation-test',idempotency_key='observation-one')
        build_result(self.project,ROOT,result_id='PR001',generated_at='2026-08-12T18:00:00+09:00',production_commit='a'*40)
        request=copy.deepcopy(self.request);request['phase']='observed'
        with self.assertRaisesRegex(ValueError,'SIMULATION_NOT_OBSERVED'):memory.capture(self.project,request)

    def test_same_result_different_title_id_does_not_duplicate_knowledge(self):
        self.source();first=self.put()
        request=copy.deepcopy(self.request);request['record_id']='renamed-spacing-study'
        again=self.put(request,operation='renamed-source',parent=first['target_commit'])
        self.assertEqual('NO_CHANGE',again['status']);self.assertEqual(first['target_commit'],again['target_commit'])
        self.assertEqual(['spacing-study'],again['accepted_ids'])
        self.assertEqual(1,len(self.get()['candidates']))

if __name__=='__main__':unittest.main()
