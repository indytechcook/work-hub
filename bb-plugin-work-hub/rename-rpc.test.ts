import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,readdirSync,symlinkSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {createFakePluginHost} from '@get-bb/plugin-sdk/testing';
import plugin from './server';

test('rename RPC validates, persists, broadcasts, and rejects a stale competing save',async()=>{
 const root=mkdtempSync(join(tmpdir(),'work-hub-rename-'));
 const previous={...process.env};
 let harness:ReturnType<typeof createFakePluginHost>['harness']|undefined;
 try{
  for(const key of Object.keys(process.env))if(key.startsWith('WORK_HUB_'))delete process.env[key];
  const config=join(root,'config.json');writeFileSync(config,'{}',{mode:0o600});
  process.env.WORK_HUB_STORAGE='json';process.env.WORK_HUB_CONFIG_FILE=config;
  mkdirSync(join(root,'.state'));
  const hub=resolve(import.meta.dirname,'../hub');
  for(const file of readdirSync(hub))if(file.endsWith('.py'))symlinkSync(join(hub,file),join(root,file));
  const python=resolve(hub,'../.venv/bin/python');
  const wid=execFileSync(python,['-c',"from store import Store,digest; import sys; s=Store(sys.argv[1]); s.ingest('bb',[dict(external_id='thr_fixture',title='Source conversation',text='Synthetic progress')]); print(digest('bb','thr_fixture')); s.close()",join(root,'.state/work.db')],{cwd:root,encoding:'utf8'}).trim();
  const host=createFakePluginHost({pluginId:'work-hub',settings:{hubPath:root,pythonPath:python,storageBackend:'json',storageFile:config,collectThreads:false,autoContext:false,remindersEnabled:false}});
  harness=host.harness;await plugin(host.bb);
  const call=(method:string,input:unknown)=>harness!.behavior.callRpc(method,input) as Promise<any>;
  const initial=await call('workDetail',{workId:wid});assert.equal(initial.work.title_version,0);
  assert.deepEqual(await call('renameWork',{workId:wid,version:0,title:'  Slack · Chosen name  '}),{title:'Slack · Chosen name',title_version:1});
  const current=await call('workDetail',{workId:wid});assert.equal(current.work.title,'Slack · Chosen name');assert.equal(current.work.conversations[0].title,'Source conversation');
  await assert.rejects(call('renameWork',{workId:wid,version:0,title:'Stale'}),/changed in another view/);
  for(const title of ['','a\nb','x'.repeat(201)])await assert.rejects(call('renameWork',{workId:wid,version:1,title}));
  const context=await call('threadContext',{threadId:'thr_fixture'});assert.equal(context.work.title,'Slack · Chosen name');
  assert.equal((await call('workIndex',{query:'Chosen name',status:'all',offset:0})).items[0].title,'Slack · Chosen name');
  assert.equal((await call('workDetail',{workId:wid})).work.title_version,1);
  assert.equal(harness.realtimeSignals.filter(s=>s.channel==='changed').length,1);
 }finally{
  await harness?.lifecycle.dispose();
  for(const key of Object.keys(process.env))if(key.startsWith('WORK_HUB_'))delete process.env[key];
  for(const [key,value] of Object.entries(previous))if(key.startsWith('WORK_HUB_'))process.env[key]=value;
  rmSync(root,{recursive:true,force:true});
 }
});
