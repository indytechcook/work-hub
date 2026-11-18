"""Project names for captured Linear references, read through BB's bound-team panel."""
import json
import subprocess
from collections import defaultdict
from references import reference


def linear_rpc(method, request):
    response=subprocess.run(['bb','plugin','rpc','call','linear',method,'--input-file','/dev/stdin','--json'],
                            input=json.dumps(request),text=True,capture_output=True,check=True,timeout=60)
    return json.loads(response.stdout)


def refresh_projects(store, rpc=linear_rpc):
    targets={r[0] for r in store.db.execute("SELECT DISTINCT key FROM source_references WHERE source='linear'")}
    projects={}
    def read(filters, search=''):
        result=rpc('panel',dict(grouping='project',filters=dict(includeCompleted=True,**filters),search=search))
        state=result['state']
        if result.get('notice') or state['kind'] not in ('rows','empty-team','empty-filter'):
            raise RuntimeError('Linear project list is unavailable')
        if state['kind']!='rows':return False
        for group in state['groups']:
            for issue in group['rows']:
                ref=reference(issue['url'])
                if ref and ref['key'] in targets:
                    projects[ref['key']]=None if group['key']=='no-project' else group['label']
        return state['shown']<state['total']

    # Panel rows are capped. Query disjoint workflow states and, for a large
    # state, issue-key prefixes. Persist only exact captured workspace/issue links.
    if targets and read({}):
        for state in rpc('facets',{'team':None})['states']:
            filters={'stateIds':[state['id']]}
            if not read(filters):continue
            pending=defaultdict(set)
            for key in targets-projects.keys():
                identifier=key.rsplit(':',1)[1]
                pending[identifier.split('-')[0]+'-'].add(identifier)
            while pending:
                prefix,ids=pending.popitem()
                if read(filters,prefix):
                    for identifier in ids:
                        if len(identifier)>len(prefix):
                            pending[identifier[:len(prefix)+1]].add(identifier)
    with store.db:
        store.db.execute("DELETE FROM meta WHERE key GLOB 'project_for:linear:*'")
        for key,name in projects.items():
            if name:store._put_record('project_for:'+key,name)
    return sum(bool(name) for name in projects.values())
