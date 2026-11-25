import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,readdirSync,symlinkSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {createFakePluginHost} from '@get-bb/plugin-sdk/testing';
import plugin from './server';

test('work status changes tell the Chief of Staff; other mutations and its absence do not break mutate',async()=>{
 const root=mkdtempSync(join(tmpdir(),'work-hub-cos-'));
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
  const notices:Array<{pluginId:string,method:string,input:unknown}>=[];
  let cosDown=false;
  const host=createFakePluginHost({sdk:{plugins:{callRpc:async(args:{pluginId:string,method:string,input:unknown})=>{notices.push({pluginId:args.pluginId,method:args.method,input:args.input});if(cosDown)throw new Error('unknown plugin chief-of-staff');return {ok:true,closed:true};}}},pluginId:'work-hub',settings:{hubPath:root,pythonPath:python,storageBackend:'json',storageFile:config,collectThreads:false,autoContext:false,remindersEnabled:false}});
  harness=host.harness;await plugin(host.bb);
  const call=(method:string,input:unknown)=>harness!.behavior.callRpc(method,input) as Promise<any>;
  const status=async()=>(await call('workDetail',{workId:wid})).work.status;

  assert.deepEqual(await call('mutate',{kind:'work',id:wid,action:'done'}),{ok:true});
  assert.equal(await status(),'done');
  assert.deepEqual(notices,[{pluginId:'chief-of-staff',method:'workChanged',input:{workId:wid}}]);

  // A reopen is a status change too; the Chief of Staff re-reads it rather than trusting notice order.
  await call('mutate',{kind:'work',id:wid,action:'active'});
  await call('mutate',{kind:'work',id:wid,action:'waiting'});
  assert.deepEqual(notices.map(n=>n.input),[{workId:wid},{workId:wid},{workId:wid}]);

  await call('mutate',{kind:'work',id:wid,action:'unfocus'});
  assert.equal(notices.length,3,'focus changes are not status changes');

  cosDown=true;
  assert.deepEqual(await call('mutate',{kind:'work',id:wid,action:'done'}),{ok:true});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(await status(),'done','a missing Chief of Staff never blocks the status change');
  assert.equal(notices.length,4);
  assert.equal(harness.inspection.logEntries.filter(l=>l.level==='warn').length,0,'an absent plugin is expected, not a warning');
 }finally{
  await harness?.lifecycle.dispose();
  for(const key of Object.keys(process.env))if(key.startsWith('WORK_HUB_'))delete process.env[key];
  for(const [key,value] of Object.entries(previous))if(key.startsWith('WORK_HUB_'))process.env[key]=value;
  rmSync(root,{recursive:true,force:true});
 }
});
