"""Prepare an exact-target review from existing native approvals; never grant consent."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.lib.canonical import canonical_sha256, sha256_bytes
from tools.lib.diagnostics import DiagnosticError
from tools.lib.runtime import Runtime
from tools.lib.yaml_io import load_yaml
from tools.public_plan_attestation import ROOT, linked_assets, public_text, read_asset
from tools.validate import validate_project

CONSTRAINTS = ['public-plan-review:content_safety=PASSED', 'public-plan-review:rights=PASSED', 'public-plan-review:consent=PASSED']


def prepare(project_root, *, occurred_at):
    project = Path(project_root).resolve()
    if project == ROOT or ROOT in project.parents:
        raise ValueError('EXTERNAL_PROJECT_REQUIRED')
    findings = validate_project(project, ROOT, check_attestation=False)
    if findings:
        return {'contract_version':'public-plan-review-packet/v1', 'status':'BLOCKED_PLAN',
                'findings':[f.as_dict() for f in findings], 'review':None,
                'next_action':{'actor':'agent', 'do':'Repair the native plan findings and regenerate the canonical plan.'}}
    body = (project/'03_plan/production-plan.md').read_bytes()
    public_text(body.decode('utf-8'))
    aggregate = (project/'03_plan/production-plan.yaml').read_bytes()
    plan = load_yaml(project/'03_plan/production-plan.yaml')
    assets = []
    for relative in sorted(linked_assets(body.decode('utf-8'))):
        media = {'.svg':'image/svg+xml', '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg'}.get(Path(relative).suffix.lower())
        if media is None:
            raise ValueError('PUBLIC_ASSET_MEDIA_TYPE')
        raw = read_asset(project, relative, media)
        assets.append({'path':relative,'sha256':sha256_bytes(raw),'byte_length':len(raw),'media_type':media})
    target = {'aggregate_sha256':sha256_bytes(aggregate),'body_sha256':sha256_bytes(body),'assets':assets}
    target_hash = canonical_sha256(target)
    target_ref = 'public-plan-review/' + plan['plan_id']
    requirement = {'id':'APR001','action':'PUBLICATION','target_ref':target_ref,'target_sha256':target_hash,'authority':'HUMAN'}
    packet = {'contract_version':'public-plan-review-packet/v1','status':'BLOCKED_REVIEW',
              'target':target,'requirement':requirement,'required_constraints':CONSTRAINTS,
              'findings':[], 'review':None, 'external_effects_authorized':False,
              'next_action':{'actor':'agent','do':'Resolve existing native approval records for this exact target; request only missing review decisions. No publication is performed.'}}
    runtime = Runtime(project, ROOT)
    try:
        _, state = runtime._checked_runtime()
        native_plan = {'approval_register':{'requirements':[requirement]}}
        approval_ids = runtime._valid_approval_ids(state, ['APR001'], occurred_at=occurred_at, plan=native_plan)
        approval = runtime._latest_approvals(state)[approval_ids[0]]
        missing = sorted(set(CONSTRAINTS)-set(approval['constraints']))
        if missing:
            packet['findings']=[{'rule':'PUBLIC_REVIEW_DECISIONS_MISSING','missing':missing}]
            packet['next_action']={'actor':'human','do':'Review only the listed missing decisions for the prepared target; record a new native approval revision.'}
            return packet
    except DiagnosticError as exc:
        packet['findings']=[exc.finding.as_dict()]
        if exc.finding.rule in {'RUNTIME_APPROVAL_REQUIRED','RUNTIME_APPROVAL_REVOKED','RUNTIME_APPROVAL_EXPIRED','RUNTIME_APPROVAL_HASH_MISMATCH'}:
            packet['next_action']={'actor':'human','do':'Review the prepared target and missing native approval finding; reuse a valid existing approval if one is available.'}
        return packet
    ref = 'approval/' + approval['approval_id'] + '/revision/' + str(approval['revision'])
    review = {'contract_version':'public-plan-review/v1','policy_version':'public-plan-policy/v1',
              'aggregate_sha256':target['aggregate_sha256'],'body_sha256':target['body_sha256'],
              'content_safety':'PASSED','rights':'PASSED','consent':'PASSED','consent_ref':ref,
              'assets':[{**a,'rights_status':'PUBLIC_CLEARED','rights_ref':ref} for a in assets]}
    packet.update(status='REVIEW_READY', review=review, next_action={'actor':'agent','do':'Generate the Production attestation using this review; then resume the original delivery run.'},
                  approval_ref=ref, approval_sha256=canonical_sha256(approval), checked_at=occurred_at)
    return packet


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,required=True)
    parser.add_argument('--at', default=None)
    parser.add_argument('--review-output',type=Path)
    args=parser.parse_args(argv)
    try:
        result=prepare(args.project_root,occurred_at=args.at or datetime.now(timezone.utc).isoformat())
        if args.review_output and result['status']=='REVIEW_READY':
            # Create-only; revisions need distinct files, never overwrite prior evidence.
            with args.review_output.open('x',encoding='utf-8') as stream:
                json.dump(result['review'],stream,ensure_ascii=False,sort_keys=True); stream.write('\n')
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0 if result['status']=='REVIEW_READY' else 2
    except (OSError,ValueError,KeyError,DiagnosticError) as exc:
        print(json.dumps({'status':'BLOCKED_REVIEW','rule':type(exc).__name__,'reason':str(exc),'next_action':{'actor':'agent','do':'Repair review inputs and resume the same project.'}}))
        return 2

if __name__=='__main__':
    raise SystemExit(main())
