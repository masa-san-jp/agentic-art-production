#!/usr/bin/env python3
"""Curated Production knowledge: native evidence references, owner Git and conditional reuse."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.lib.canonical import canonical_sha256
from tools.lib.config import load_config
from tools.lib.diagnostics import DiagnosticError
from tools.lib.result import validate_result
from tools.lib.schema import load_schema, validate_instance
from tools.lib.security import check_text_security
from tools.lib.yaml_io import load_yaml

ROOT = Path(__file__).resolve().parents[1]
REF = 'refs/heads/knowledge'
OWNER = 'agentic-art-production'
PREFIX = 'knowledge/production/'
SHA = re.compile(r'^[0-9a-f]{40}$')
SLUG = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
# Orchestration run and operation IDs retain their AAK-prefixed provenance.
# They are path-safe identifiers but may contain uppercase letters.
RUN_IDENTIFIER = re.compile(r'^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    require(result.tzinfo is not None, 'TIMEZONE_REQUIRED')
    return result


def git(root, *args, content=None, env=None):
    result = subprocess.run(['git','-C',str(root),*args], input=content, capture_output=True, env=env)
    require(result.returncode == 0, 'GIT_OPERATION_FAILED')
    return result.stdout.decode().strip()


def safe_root(value):
    root = Path(value)
    require(root.is_absolute() and not any(p.is_symlink() for p in [root,*root.parents]), 'STORE_PATH_INVALID')
    root = root.resolve()
    require(not root.is_relative_to(ROOT) and not ROOT.is_relative_to(root), 'STORE_CODE_OVERLAP')
    return root


def commit(root, parent, updates):
    with tempfile.TemporaryDirectory(prefix='production-memory-index-') as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory)/'index'), GIT_AUTHOR_NAME='Production memory', GIT_AUTHOR_EMAIL='memory@localhost', GIT_COMMITTER_NAME='Production memory', GIT_COMMITTER_EMAIL='memory@localhost')
        git(root,'read-tree',parent if parent else '--empty',env=env)
        for name, value in sorted(updates.items()):
            blob = git(root,'hash-object','-w','--stdin',content=encoded(value),env=env)
            git(root,'update-index','--add','--cacheinfo',f'100644,{blob},{name}',env=env)
        tree = git(root,'write-tree',env=env)
        target = git(root,'commit-tree',tree,*(['-p',parent] if parent else []),'-m','Persist curated production knowledge',env=env)
        outcome = subprocess.run(['git','-C',str(root),'update-ref',REF,target,parent or '0'*40],capture_output=True)
        require(outcome.returncode == 0, 'PARENT_CONFLICT')
        require(git(root,'rev-parse',target+'^{tree}') == tree, 'COMMIT_VERIFICATION_FAILED')
        return target


def init(root, *, creator, instance, collection):
    root = safe_root(root)
    require(all(SLUG.fullmatch(v) for v in [creator,instance,collection]), 'IDENTITY_INVALID')
    require(not root.exists(), 'STORE_EXISTS')
    root.mkdir(parents=True)
    git(root,'init','--bare','--quiet'); git(root,'symbolic-ref','HEAD',REF)
    identity = {'contract_version':'production-memory-store/v1','owner':OWNER,'creator_id':creator,'origin_instance_id':instance,'collection_id':collection}
    return {'status':'COMMITTED','target_commit':commit(root,None,{PREFIX+'store.json':identity})}


def read(root, *, creator, collection, snapshot=None):
    root = safe_root(root)
    head = git(root,'rev-parse',REF)
    snapshot = snapshot or head
    require(SHA.fullmatch(snapshot), 'SNAPSHOT_REQUIRED')
    require(subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',snapshot,head],capture_output=True).returncode == 0, 'FOREIGN_SNAPSHOT')
    identity = json.loads(git(root,'show',snapshot+':'+PREFIX+'store.json'))
    require(identity['owner'] == OWNER and identity['creator_id'] == creator and identity['collection_id'] == collection, 'OWNER_SCOPE_MISMATCH')
    files = git(root,'ls-tree','-r','--name-only',snapshot).splitlines()
    documents = {path:json.loads(git(root,'show',snapshot+':'+path)) for path in files if path.startswith(PREFIX+'records/')}
    for document in documents.values():
        require(set(document)=={'record','payload'}, 'KNOWLEDGE_DOCUMENT_FIELDS')
        record=document['record']; request=document['payload']['request']
        require(set(record)==set('contract_version record_id revision origin_instance_id creator_id owner_repository collection_id kind payload_schema payload_ref content_sha256 sources derived_from epistemic_status lifecycle applicability rights access_scope consent_ref created_at reviewed_at valid_until producer supersedes invalidates'.split()), 'ENVELOPE_FIELDS')
        require(record['contract_version']=='artifact-record/v1' and record['payload_schema']=='production-memory/v1', 'CONTRACT_VERSION')
        require(record['record_id']==request['record_id'] and record['revision']==request['revision'] and record['kind']==request['kind'], 'RECORD_REQUEST_MISMATCH')
        require(record['origin_instance_id']==identity['origin_instance_id'], 'ORIGIN_MISMATCH')
        require(record['epistemic_status']=={'planned':'proposed','simulated':'simulated','prototyped':'observed','observed':'observed'}[request['phase']], 'EPISTEMIC_PROMOTION')
        require(record['lifecycle']==('revoked' if any(o['status']=='RETRACTED' for o in document['payload']['observations']) else 'accepted'), 'LIFECYCLE_MISMATCH')
        require(record['payload_ref']==PREFIX+'payloads/'+record['record_id']+'/'+str(record['revision'])+'.json', 'PAYLOAD_LOCATOR')
        require(document['record']['content_sha256'] == digest(encoded(document['payload'])), 'KNOWLEDGE_HASH_MISMATCH')
        require(document['record']['creator_id']==creator and document['record']['collection_id']==collection and document['record']['owner_repository']==OWNER, 'KNOWLEDGE_OWNER_MISMATCH')
        expected=encoded(document['payload'])
        stored=subprocess.run(['git','-C',str(root),'show',snapshot+':'+document['record']['payload_ref']],capture_output=True)
        require(stored.returncode==0 and stored.stdout==expected,'PAYLOAD_LOCATOR_HASH_MISMATCH')
        validate_payload(document['payload'])
    return root, snapshot, identity, documents


def validate_payload(payload):
    require(set(payload)=={'request','project_id','handoff_sha256','plan_sha256','result_ref','observations','procedure','source_snapshot','knowledge_refs'}, 'PAYLOAD_FIELDS')
    path = ROOT/'schemas/production-knowledge-request.schema.json'
    findings = validate_instance(payload['request'],load_schema(path),schema_path=path)
    require(not findings, 'KNOWLEDGE_REQUEST_SCHEMA')
    require(re.fullmatch(r'production/[a-z0-9]+(?:-[a-z0-9]+)*',payload['project_id']), 'SOURCE_PROJECT_ID')
    require(isinstance(payload['source_snapshot'],dict) and payload['source_snapshot'], 'SOURCE_SNAPSHOT_REQUIRED')
    require(all(isinstance(v,str) and re.fullmatch(r'[0-9a-f]{64}',v) for v in payload['source_snapshot'].values()), 'SOURCE_SNAPSHOT_HASH')
    common=load_schema(ROOT/'schemas/common.schema.json')
    schema=load_schema(ROOT/'schemas/observation-record.schema.json')
    for observation in payload['observations']:
        require(not validate_instance(observation,schema,schema_path=ROOT/'schemas/observation-record.schema.json',common_schema=common), 'NATIVE_OBSERVATION_SCHEMA')
    require(payload['procedure'].get('evidence_status')=='NOT_RUN', 'PROPOSED_PROCEDURE_NOT_ACTUAL')
    policy = load_config(ROOT,'safety-policy.yaml')
    require(not check_text_security(payload,file='production knowledge',forbidden_markers=policy['forbidden_markers'],signed_url_markers=policy['signed_url_markers']), 'KNOWLEDGE_SECURITY')
    require(len(encoded(payload)) <= load_config(ROOT,'production-memory.yaml')['max_record_bytes'], 'KNOWLEDGE_TOO_LARGE')


def capture(project, request):
    from tools.validate import validate_project
    project = Path(project).resolve()
    require(not project.is_relative_to(ROOT), 'EXTERNAL_PROJECT_REQUIRED')
    findings = validate_project(project, ROOT)
    require(not findings, 'SOURCE_PROJECT_INVALID: '+(findings[0].rule if findings else ''))
    schema_path = ROOT/'schemas/production-knowledge-request.schema.json'
    require(not validate_instance(request,load_schema(schema_path),schema_path=schema_path), 'KNOWLEDGE_REQUEST_SCHEMA')
    require(stamp(request['valid_until']) > stamp(request['created_at']), 'VALIDITY_INTERVAL')
    plan = load_yaml(project/'03_plan/production-plan.yaml')
    tasks = {t['id']:t for t in plan['tasks']}
    require(request['task_id'] in tasks, 'SOURCE_TASK_MISSING')
    source_files = ['03_plan/production-plan.yaml','00_handoff/production-handoff.yaml']
    observation_path = project/'05_execution/observations.yaml'
    records = load_yaml(observation_path).get('records',[]) if observation_path.exists() else []
    latest = {}
    for record in records:
        if record['observation_id'] not in latest or record['revision'] > latest[record['observation_id']]['revision']:
            latest[record['observation_id']] = record
    observations = []
    for identity in request['observation_ids']:
        require(identity in latest, 'SOURCE_OBSERVATION_MISSING')
        record = latest[identity]
        require(record['privacy_status'] in {'CLEAR','PROJECT_INTERNAL'}, 'SOURCE_PRIVACY_UNRESOLVED')
        require(any(ref.get('kind')=='TASK' and ref.get('id')==request['task_id'] for ref in record['source_refs']), 'SOURCE_TASK_CONTRADICTION')
        if request['phase'] in {'observed','prototyped'}:
            require('synthetic' not in encoded(record).decode().lower() and 'simulat' not in encoded(record).decode().lower(), 'SIMULATION_NOT_OBSERVED')
            evidence_refs=[ref for ref in record['source_refs'] if 'evidence_id' in ref]
            require(evidence_refs, 'OBSERVATION_EVIDENCE_REQUIRED')
            register=load_yaml(project/'05_execution/evidence-register.yaml')
            for ref in evidence_refs:
                evidence=next((item for item in register.get('records',[]) if item['evidence_id']==ref['evidence_id'] and item['revision']==ref['revision']),None)
                require(evidence is not None and evidence['verification_status']=='VERIFIED', 'OBSERVATION_EVIDENCE_REQUIRED')
                require('synthetic' not in encoded(evidence).decode().lower() and 'simulat' not in encoded(evidence).decode().lower(), 'SIMULATION_NOT_OBSERVED')
        if request['phase']=='prototyped':
            require(any(ref.get('kind')=='PROTOTYPE' for ref in record['source_refs']), 'PROTOTYPE_REFERENCE_REQUIRED')
            control_path=project/'04_prototype/prototype-control.yaml'
            require(control_path.is_file(), 'PROTOTYPE_RESULT_REQUIRED')
            require(any(test.get('result') in {'PASS','FAIL'} and test.get('external_validation_status')=='VERIFIED' and test.get('evidence_refs') for test in load_yaml(control_path).get('test_results',[])), 'PROTOTYPE_RESULT_REQUIRED')
        observations.append(record)
    if request['phase'] != 'planned' and not observations:
        return None
    if request['phase'] == 'planned':
        require(not observations, 'PLANNED_ACTUALS_MIXED')
    for name in ['05_execution/observations.yaml','05_execution/production-log.jsonl','05_execution/evidence-register.yaml','04_prototype/prototype-control.yaml']:
        if (project/name).exists(): source_files.append(name)
    result_path = project/'08_runtime/production-result.yaml'
    result_ref = None
    if result_path.exists():
        result = load_yaml(result_path); validate_result(result,repository=ROOT)
        require(result['production_project_id']==plan['project_id'], 'RESULT_PROJECT_MISMATCH')
        active = {o['id']:o for o in result['observations']}
        for observation in observations:
            expected={k:observation[k] for k in ['statement','method','limitations','related_requirement_ids']}
            expected['id']=observation['observation_id']
            require((active.get(expected['id'])==expected) if observation['status']=='ACTIVE' else expected['id'] not in active, 'RESULT_OBSERVATION_STALE')
        result_ref={'result_id':result['result_id'],'content_sha256':result['integrity']['content_sha256'],'production_commit':result['production_commit']}
        source_files.append('08_runtime/production-result.yaml')
    if observations:
        require(result_ref is not None, 'SOURCE_RESULT_REQUIRED')
    methods = plan.get('production_method',{}).get('steps',[])
    procedure = next((s for s in methods if s['task_id']==request['task_id']),None)
    if procedure is None:
        procedure = {'task_id':request['task_id'],'instruction':tasks[request['task_id']]['title'],'completion_check':tasks[request['task_id']]['acceptance_condition'],'evidence_status':'NOT_RUN'}
    payload={'request':request,'project_id':plan['project_id'],'handoff_sha256':plan['handoff_ref']['content_sha256'],'plan_sha256':plan['integrity']['content_sha256'],'result_ref':result_ref,'observations':observations,'procedure':procedure,'source_snapshot':{name:digest((project/name).read_bytes()) for name in sorted(source_files)},'knowledge_refs':[{k:c[k] for k in ['record_id','revision','origin_instance_id','knowledge_commit','content_sha256']} for c in plan.get('knowledge_reuse',{}).get('choices',[]) if c['status']=='ADOPTED']}
    validate_payload(payload)
    return payload


def reindex(root, snapshot, documents):
    index = {'knowledge_commit':snapshot,'records':[d['record'] for _,d in sorted(documents.items())]}
    target = root/'production-memory-index.json'
    with tempfile.NamedTemporaryFile(dir=root,prefix='production-index-',delete=False) as stream:
        temporary=Path(stream.name);stream.write(encoded(index))
    try: temporary.replace(target)
    finally: temporary.unlink(missing_ok=True)
    return digest(encoded(index))


def ingest(root, project, request, *, creator, collection, operation, run_id, parent):
    root, head, identity, documents = read(root,creator=creator,collection=collection)
    require(RUN_IDENTIFIER.fullmatch(operation) and RUN_IDENTIFIER.fullmatch(run_id), 'OPERATION_ID_INVALID')
    code = git(ROOT,'rev-parse','HEAD')
    require(not git(ROOT,'status','--porcelain'), 'CLEAN_CODE_REQUIRED')
    payload = capture(project,request)
    receipt={'contract_version':'knowledge-write-receipt/v1','operation_id':operation,'run_id':run_id,'owner':OWNER,'collection':collection,'target_parent':head,'target_commit':head,'accepted_ids':[],'rejected_ids':[],'schema_version':'production-memory/v1','policy_version':'production-memory-policy/v1','index_commit':None,'index_hash':None,'status':'NO_CHANGE','reason':'NO_NEW_EVIDENCE'}
    if payload is None: return receipt
    payload_hash = digest(encoded(payload)); rid=request['record_id']; revision=request['revision']
    path=PREFIX+'records/'+rid+'/'+str(revision)+'.json'
    existing=documents.get(path)
    op_path=PREFIX+'operations/'+operation+'.json'
    files=git(root,'ls-tree','-r','--name-only',head).splitlines()
    if op_path in files:
        previous=json.loads(git(root,'show',head+':'+op_path))
        require(previous['payload_sha256']==payload_hash and previous['run_id']==run_id, 'OPERATION_CONFLICT')
    def source_key(value):
        return canonical_sha256({'project':value['project_id'],'plan':value['plan_sha256'],'result':value['result_ref'],'observations':[[o['observation_id'],o['revision']] for o in value['observations']],'task':value['request']['task_id'],'kind':value['request']['kind'],'phase':value['request']['phase']})
    duplicate=next((d for d in documents.values() if d['record']['record_id']!=rid and source_key(d['payload'])==source_key(payload)),None)
    if duplicate is not None:
        receipt.update(reason='Source/result already retained under its stable record ID',accepted_ids=[duplicate['record']['record_id']])
        return receipt
    if existing:
        require(existing['record']['content_sha256']==payload_hash,'REVISION_CONFLICT')
        original=git(root,'log','--diff-filter=A','--format=%H',head,'--',path).splitlines()[-1]
        _, original, _, documents=read(root,creator=creator,collection=collection,snapshot=original)
        receipt.update(target_parent=git(root,'rev-parse',original+'^'),target_commit=original,status='ALREADY_APPLIED',reason='Exact source/result already retained',accepted_ids=[rid])
        head=original
    else:
        require(parent==head,'PARENT_CONFLICT')
        history=[d for d in documents.values() if d['record']['record_id']==rid]
        previous=max(history,key=lambda d:d['record']['revision']) if history else None
        require(revision==(previous['record']['revision']+1 if previous else 1),'REVISION_GAP')
        if previous:
            require(previous['payload']['project_id']==payload['project_id'],'RECORD_PROJECT_CHANGED')
        revoked=any(o['status']=='RETRACTED' for o in payload['observations'])
        epistemic={'planned':'proposed','simulated':'simulated','prototyped':'observed','observed':'observed'}[request['phase']]
        record={'contract_version':'artifact-record/v1','record_id':rid,'revision':revision,'origin_instance_id':identity['origin_instance_id'],'creator_id':creator,'owner_repository':OWNER,'collection_id':collection,'kind':request['kind'],'payload_schema':'production-memory/v1','payload_ref':PREFIX+'payloads/'+rid+'/'+str(revision)+'.json','content_sha256':payload_hash,'sources':[{'repository':OWNER,'project_id':payload['project_id'],'code_commit':code,'snapshot':payload['source_snapshot'],'result':payload['result_ref']}],'derived_from':[dict(ref,owner_repository=OWNER) for ref in payload['knowledge_refs']],'epistemic_status':epistemic,'lifecycle':'revoked' if revoked else 'accepted','applicability':request['conditions'],'rights':{'status':'PROJECT_INTERNAL'},'access_scope':'CREATOR_PRIVATE','consent_ref':None,'created_at':request['created_at'],'reviewed_at':None,'valid_until':request['valid_until'],'producer':{'repository':OWNER,'code_commit':code,'run_id':run_id,'generator_version':'production-memory/v1','tool':'tools/production_memory.py'},'supersedes':[{'record_id':rid,'revision':revision-1}] if previous else [],'invalidates':[{'record_id':rid,'revision':revision-1}] if revoked and previous else []}
        document={'record':record,'payload':payload}; target=commit(root,head,{path:document,record['payload_ref']:payload,op_path:{'payload_sha256':payload_hash,'run_id':run_id}})
        documents[path]=document;head=target
        receipt.update(target_commit=target,accepted_ids=[rid],status='COMMITTED',reason='Curated native source persisted; no execution authority transferred')
    try:
        receipt.update(index_commit=head,index_hash=reindex(root,head,documents))
    except OSError:
        receipt.update(status='INDEX_PENDING',reason='Knowledge commit retained; retry indexing the committed snapshot')
    return receipt


def query(root, *, creator, collection, snapshot, project_id, environment, at):
    root, snapshot, identity, documents=read(root,creator=creator,collection=collection,snapshot=snapshot)
    now=stamp(at);latest={}
    for document in documents.values():
        record=document['record']; rid=record['record_id']
        if rid not in latest or record['revision']>latest[rid]['record']['revision']: latest[rid]=document
    uses=[]
    for path in git(root,'ls-tree','-r','--name-only',snapshot).splitlines():
        if path.startswith(PREFIX+'uses/'):
            uses.append(json.loads(git(root,'show',snapshot+':'+path)))
    candidates=[]
    for rid, document in sorted(latest.items()):
        record,payload=document['record'],document['payload'];conditions=record['applicability']; reasons=[]
        status='APPLICABLE'
        mismatch=[key for key in load_config(ROOT,'production-memory.yaml')['required_conditions'] if key!='as_of' and environment.get(key)!=conditions.get(key)]
        if mismatch: status='NOT_APPLICABLE';reasons=['environment mismatch: '+','.join(mismatch)]
        if record['lifecycle']!='accepted' or stamp(record['valid_until'])<=now or environment.get('as_of')!=conditions['as_of'] or any(str(v).upper()=='UNKNOWN' for v in conditions.values()):
            status='REVALIDATE';reasons.append('source revoked, expired or conditions require fresh verification')
        candidates.append({'record':record,'payload':payload,'knowledge_commit':snapshot,'status':status,'reasons':reasons,'relationship':'SAME_PROJECT_REVISION' if payload['project_id']==project_id else 'CROSS_PROJECT_REUSE','affected_projects':sorted({use['project_id'] for use in uses if any(ref['record_id']==rid and (ref['revision']<record['revision'] or status=='REVALIDATE') for ref in use['sources'])})})
    return {'contract_version':'production-memory-query/v1','knowledge_commit':snapshot,'history_status':'FOUND' if candidates else 'NO_NEW_EVIDENCE','candidates':candidates,'knowledge_status':'READABLE','plan_completion_blocked':False}



def remember_use(root, project, *, creator, collection, parent):
    from tools.validate import validate_project
    project=Path(project).resolve()
    require(not validate_project(project,ROOT), 'REUSE_PLAN_INVALID')
    plan=load_yaml(project/'03_plan/production-plan.yaml')
    report=plan.get('knowledge_reuse')
    require(report and report['creator_id']==creator and report['collection_id']==collection, 'REUSE_SCOPE_MISMATCH')
    root,head,identity,documents=read(root,creator=creator,collection=collection)
    sources=[]
    for choice in report['choices']:
        if choice['status']!='ADOPTED': continue
        latest=max((d['record'] for d in documents.values() if d['record']['record_id']==choice['record_id']),key=lambda r:r['revision'])
        require(latest['revision']==choice['revision'] and latest['content_sha256']==choice['content_sha256'] and latest['lifecycle']=='accepted','REUSE_SOURCE_CHANGED')
        sources.append({'record_id':choice['record_id'],'revision':choice['revision'],'knowledge_commit':choice['knowledge_commit'],'content_sha256':choice['content_sha256'],'target_task_id':choice['target_task_id'],'reason':choice['reason']})
    if not sources:return {'status':'NO_CHANGE','reason':'NO_ADOPTED_KNOWLEDGE','target_commit':head}
    use={'project_id':plan['project_id'],'plan_sha256':plan['integrity']['content_sha256'],'sources':sources}
    path=PREFIX+'uses/'+digest(encoded([use['project_id'],use['plan_sha256']]))+'.json'
    if path in git(root,'ls-tree','-r','--name-only',head).splitlines():
        require(json.loads(git(root,'show',head+':'+path))==use,'REUSE_IMMUTABILITY_CONFLICT')
        return {'status':'ALREADY_APPLIED','target_commit':head,'use_ref':path}
    require(parent==head,'PARENT_CONFLICT')
    target=commit(root,head,{path:use})
    return {'status':'COMMITTED','target_parent':head,'target_commit':target,'use_ref':path}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--store',required=True,type=Path);parser.add_argument('--creator',required=True);parser.add_argument('--collection',required=True)
    commands=parser.add_subparsers(dest='command',required=True)
    init_parser=commands.add_parser('init');init_parser.add_argument('--instance',required=True)
    put=commands.add_parser('ingest');put.add_argument('--project',required=True,type=Path);put.add_argument('--request',required=True,type=Path);put.add_argument('--operation',required=True);put.add_argument('--run-id',required=True);put.add_argument('--parent',required=True)
    get=commands.add_parser('query');get.add_argument('--snapshot',required=True);get.add_argument('--project-id',required=True);get.add_argument('--environment',required=True,type=Path);get.add_argument('--at',required=True)
    usage=commands.add_parser('remember-use');usage.add_argument('--project',required=True,type=Path);usage.add_argument('--parent',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='init': result=init(args.store,creator=args.creator,instance=args.instance,collection=args.collection)
        elif args.command=='ingest':result=ingest(args.store,args.project,load_yaml(args.request),creator=args.creator,collection=args.collection,operation=args.operation,run_id=args.run_id,parent=args.parent)
        elif args.command=='remember-use':result=remember_use(args.store,args.project,creator=args.creator,collection=args.collection,parent=args.parent)
        else:result=query(args.store,creator=args.creator,collection=args.collection,snapshot=args.snapshot,project_id=args.project_id,environment=load_yaml(args.environment),at=args.at)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError,KeyError,TypeError,DiagnosticError) as exc:
        print(json.dumps({'status':'REJECTED','reason':str(exc)},ensure_ascii=False));return 1

if __name__=='__main__':raise SystemExit(main())
