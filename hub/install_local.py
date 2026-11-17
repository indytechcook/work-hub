"""Install only the configured user's local collection/analysis jobs."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from analyzer_runtime import settings as analyzer_settings
from settings import identity,selected_sources
from storage import Store

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'.state'
LABELS={'collect':'local.work-hub.collect','analyze':'local.work-hub.analyze'}


def plans(context,env,sources):
    jobs={}
    for kind in (['collect','analyze'] if sources else ['analyze']):
        args=[sys.executable,str(ROOT/('collect.py' if kind=='collect' else 'worker.py'))]
        if kind=='collect':args+=sources
        jobs[kind]={'Label':LABELS[kind],'ProgramArguments':args,'WorkingDirectory':str(ROOT.parent),
                    'EnvironmentVariables':env,'RunAtLoad':True,'StartInterval':600 if kind=='collect' else 90,
                    'StandardOutPath':str(STATE/(kind+'.log')),'StandardErrorPath':str(STATE/(kind+'.error.log')),
                    'ProcessType':'Background','Umask':63}
    return jobs


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    if sys.platform!='darwin':raise RuntimeError('This installer currently supports macOS only')
    if any(key.startswith('WORK_HUB_') and key.endswith('_JSON') for key in os.environ):
        raise ValueError('Environment JSON overrides are not copied to launchd; use BB storage or an explicit JSON bundle for scheduled jobs')
    identity();analyzer_settings(STATE);sources=selected_sources()
    context=Store(STATE).read('bb_context') or {}
    project=context['project']['id']
    if not isinstance(project,str) or not project.startswith('proj_'):raise ValueError('Capture your BB project context first')
    env={'PATH':os.environ['PATH'],'HOME':str(Path.home()),'BB_PROJECT_ID':project}
    for key in ('BB_CLI','BB_SERVER_URL','BB_SERVER_TOKEN','BB_DATA_DIR','WORK_HUB_STORAGE','WORK_HUB_CONFIG_FILE','WORK_HUB_PROFILE'):
        if os.environ.get(key):env[key]=os.environ[key]
    jobs=plans(context,env,sources)
    folder=Path.home()/'Library/LaunchAgents'
    paths={kind:folder/(job['Label']+'.plist') for kind,job in jobs.items()}
    if any(path.exists() for path in paths.values()):raise RuntimeError('Existing Work Hub job; inspect and explicitly update it')
    if args.dry_run:
        print(json.dumps({'sources':sources,'jobs':[{k:job[k] for k in ('Label','ProgramArguments','StartInterval')} for job in jobs.values()]}));return
    folder.mkdir(exist_ok=True)
    if Store(STATE).read('policy') is None:Store(STATE).write('policy',{'max_analyses_per_day':120})
    written=[];loaded=[]
    try:
        for kind,job in jobs.items():
            path=paths[kind]
            with path.open('xb') as stream:stream.write(plistlib.dumps(job))
            path.chmod(0o600);written.append(path)
        for path in written:
            subprocess.run(['launchctl','bootstrap','gui/'+str(os.getuid()),str(path)],check=True)
            loaded.append(path)
    except (OSError,subprocess.CalledProcessError):
        for path in reversed(loaded):subprocess.run(['launchctl','bootout','gui/'+str(os.getuid()),str(path)],check=False)
        for path in written:path.unlink()
        raise
    for job in jobs.values():print(job['Label']+' installed')

if __name__=='__main__':os.umask(0o077);main()
