from __future__ import annotations
import copy
import unittest
from tools.public_plan_review import prepare, CONSTRAINTS
from tools.lib.runtime import Runtime
from tests import test_public_plan_attestation as fixture
ROOT, NOW = fixture.ROOT, fixture.NOW

class PublicReviewTests(unittest.TestCase):
    def setUp(self):
        fixture.PublicPlanAttestationTests.setUp(self)
        self.runtime=Runtime(self.project,ROOT)
        self.runtime.bootstrap(occurred_at=NOW,actor_kind='SYSTEM',actor_id='review-fixture')

    def approval(self):
        packet=prepare(self.project,occurred_at=NOW)
        return {'approval_id':'AP901','revision':1,'decision':'APPROVED',
                'scope':{k:packet['requirement'][k] for k in ('action','target_ref','target_sha256')},
                'approver':{'id':'synthetic-reviewer','authority':'HUMAN'},
                'issued_at':'2026-09-04T00:00:00Z','expires_at':'2026-09-06T00:00:00Z','constraints':CONSTRAINTS}

    def record(self,approval):
        self.runtime.record_approval(approval=approval,occurred_at=NOW,actor_kind='HUMAN',actor_id='synthetic-reviewer',idempotency_key='review-'+str(approval['revision']))

    def test_missing_review_prepares_exact_target(self):
        packet=prepare(self.project,occurred_at=NOW)
        self.assertEqual('BLOCKED_REVIEW',packet['status'])
        self.assertTrue(packet['target']['assets'])
        self.assertIsNone(packet['review'])

    def test_existing_native_approval_reused_and_revocation_respected(self):
        approval=self.approval(); self.record(approval)
        first=prepare(self.project,occurred_at=NOW)
        self.assertEqual('REVIEW_READY',first['status'])
        self.assertEqual(first,prepare(self.project,occurred_at=NOW))
        self.assertEqual('agent',first['next_action']['actor'])
        revoked=copy.deepcopy(approval);revoked.update(revision=2,decision='REVOKED')
        self.record(revoked)
        self.assertEqual('BLOCKED_REVIEW',prepare(self.project,occurred_at=NOW)['status'])

    def test_expiry_scope_and_missing_decisions(self):
        approval=self.approval();approval['constraints']=[];self.record(approval)
        result=prepare(self.project,occurred_at=NOW)
        self.assertEqual('PUBLIC_REVIEW_DECISIONS_MISSING',result['findings'][0]['rule'])
        expired=prepare(self.project,occurred_at='2026-09-07T00:00:00Z')
        self.assertEqual('BLOCKED_REVIEW',expired['status'])

    def test_arbitrary_review_json_is_not_a_native_approval(self):
        (self.project/'03_plan/forged-review.json').write_text('{"consent":"PASSED","consent_ref":"made-up"}')
        self.assertEqual('BLOCKED_REVIEW',prepare(self.project,occurred_at=NOW)['status'])

    def test_wrong_scope_and_hash_cannot_reuse_approval(self):
        approval=self.approval();approval['scope']['target_sha256']='sha256:'+'0'*64
        self.record(approval)
        self.assertEqual('BLOCKED_REVIEW',prepare(self.project,occurred_at=NOW)['status'])

    def test_strict_attestation_cli_rechecks_native_revocation(self):
        import contextlib,io
        from tools.public_plan_attestation import main
        approval=self.approval();approval['expires_at']='2099-01-01T00:00:00Z';self.record(approval)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0,main(['--project-root',str(self.project),'--native-review','--producer-commit',self.code,'--generated-at',NOW]))
            self.assertEqual(0,main(['--project-root',str(self.project),'--check','--require-native-review']))
            revoked=copy.deepcopy(approval);revoked.update(revision=2,decision='REVOKED');self.record(revoked)
            self.assertEqual(2,main(['--project-root',str(self.project),'--check','--require-native-review']))
