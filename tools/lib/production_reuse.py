"""Resolve owner knowledge from a pinned store into proposed plan choices."""
from pathlib import Path
from tools.lib.canonical import canonical_sha256
from tools.lib.yaml_io import load_yaml


def integrate(project, plan):
    from tools.production_memory import query, require
    path=project/'02_specification/production-memory-query.yaml'
    if not path.exists() and not path.is_symlink():return
    require(not path.is_symlink(),'MEMORY_QUERY_SYMLINK')
    request=load_yaml(path)
    require(isinstance(request,dict) and set(request)=={'contract_version','store','creator','collection','snapshot','at','environment','selections'},'MEMORY_QUERY_FIELDS')
    require(request['contract_version']=='production-memory-query-request/v1','MEMORY_QUERY_VERSION')
    require(plan.get('production_method') is not None,'METHOD_REQUIRED_FOR_REUSE')
    require(request['environment']==plan['production_method']['environment'],'METHOD_ENVIRONMENT_MISMATCH')
    result=query(Path(request['store']),creator=request['creator'],collection=request['collection'],snapshot=request['snapshot'],project_id=plan['project_id'],environment=request['environment'],at=request['at'])
    by_id={c['record']['record_id']:c for c in result['candidates']}
    choices=[];seen=set()
    for selection in request['selections']:
        require(isinstance(selection,dict) and set(selection)=={'record_id','target_task_id','reason'},'REUSE_SELECTION_FIELDS')
        require(isinstance(selection['reason'],str) and selection['reason'].strip(),'REUSE_REASON_REQUIRED')
        require(selection['record_id'] in by_id,'REUSE_RECORD_MISSING')
        key=(selection['record_id'],selection['target_task_id']);require(key not in seen,'DUPLICATE_REUSE');seen.add(key)
        candidate=by_id[selection['record_id']];record=candidate['record'];payload=candidate['payload']
        step=next((s for s in plan['production_method']['steps'] if s['task_id']==selection['target_task_id']),None)
        require(step is not None,'REUSE_TARGET_MISSING')
        choice={**selection,'revision':record['revision'],'origin_instance_id':record['origin_instance_id'],'code_commit':record['producer']['code_commit'],'knowledge_commit':request['snapshot'],'content_sha256':record['content_sha256'],'source_project_id':payload['project_id'],'relationship':candidate['relationship'],'phase':payload['request']['phase'],'status':'ADOPTED' if candidate['status']=='APPLICABLE' else candidate['status'],'reasons':candidate['reasons'],'source_conditions':record['applicability'],'source_procedure':payload['procedure']['instruction'],'use_proposal':payload['request']['use_proposal'],'interpretation':payload['request']['interpretation'],'affected_projects':candidate['affected_projects']}
        if choice['status']=='ADOPTED':
            # Explicit agent selection changes the actual step, not just its title.
            step['instruction'] += '\n参照知識からの提案（未実証）: '+choice['use_proposal']+'\n採用理由: '+selection['reason']
            plan['production_method']['knowledge_uses'].append({'target_id':step['task_id'],'code_commit':choice['code_commit'],'knowledge_commit':request['snapshot'],'record_id':record['record_id'],'source_conditions':record['applicability'],'use':selection['reason']})
        choices.append(choice)
    plan['knowledge_reuse']={'contract_version':'production-plan-reuse/v1','query_sha256':canonical_sha256({k:v for k,v in request.items() if k!='store'}),'creator_id':request['creator'],'collection_id':request['collection'],'knowledge_commit':request['snapshot'],'at':request['at'],'environment':request['environment'],'history_status':result['history_status'],'choices':choices,'knowledge_status':'READABLE','plan_completion_blocked':False}


def render(plan):
    report=plan.get('knowledge_reuse')
    if report is None:return ''
    lines=['','## 制作知識の採否','', '知識検索: '+report['history_status']+'（観察0件から実績は生成しない）','']
    for choice in report['choices']:
        lines += ['- '+choice['record_id']+' → '+choice['target_task_id']+': '+choice['status']+' / '+choice['phase'],
                  '  判断: '+choice['reason']+' / 条件: '+str(choice['source_conditions']),
                  '  元工程: '+choice['source_procedure'], '  提案: '+choice['use_proposal'],
                  '  原因等の推定（観測事実ではない）: '+choice['interpretation']['statement'],
                  '  参照: '+choice['source_project_id']+' @ '+choice['knowledge_commit'],
                  '  判定理由: '+'; '.join(choice['reasons'])]
    return '\n'.join(lines)+'\n'
