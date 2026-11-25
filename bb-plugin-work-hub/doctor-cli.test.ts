import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,readdirSync,symlinkSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {createFakePluginHost} from '@get-bb/plugin-sdk/testing';
import plugin from './server';

test('bb work-hub doctor merges plugin and hub checks without changing anything',async()=>{
 const root=mkdtempSync(join(tmpdir(),'work-hub-doctor-'));
 const previous={...process.env};
 let harness:ReturnType<typeof createFakePluginHost>['harness']|undefined;
 try{
  for(const key of Object.keys(process.env))if(key.startsWith('WORK_HUB_'))delete process.env[key];
  const config=join(root,'config.json');writeFileSync(config,'{}',{mode:0o600});
  process.env.WORK_HUB_STORAGE='json';process.env.WORK_HUB_CONFIG_FILE=config;
  mkdirSync(join(root,'.state'),{mode:0o700});
  const hub=resolve(import.meta.dirname,'../hub');
  for(const file of readdirSync(hub))if(file.endsWith('.py'))symlinkSync(join(hub,file),join(root,file));
  const python=resolve(hub,'../.venv/bin/python');
  const host=createFakePluginHost({sdk:{plugins:{list:async()=>({plugins:[{id:'chief-of-staff',enabled:true,status:'running'}]})}} as never,pluginId:'work-hub',settings:{hubPath:root,pythonPath:python,storageBackend:'json',storageFile:config,collectThreads:true,threadProjects:'',autoContext:false,remindersEnabled:false}});
  harness=host.harness;await plugin(host.bb);
  const result=await harness.behavior.runCli(['doctor','--json']);
  const {checks}=JSON.parse(String(result.stdout)) as {checks:Array<{level:string,name:string,detail:string}>};
  const level=(name:string)=>checks.find(c=>c.name===name)?.level;
  assert.equal(level('hubPath'),'ok');
  assert.equal(level('threadProjects'),'warn');
  assert.equal(level('Storage'),'ok');
  assert.equal(level('Config'),'fail');
  assert.match(checks.find(c=>c.name==='Chief of Staff')!.detail,/running/);
  assert.equal(result.exitCode,1);
  assert.equal(readdirSync(join(root,'.state')).length,0,'the doctor creates no state');
 }finally{
  await harness?.lifecycle.dispose();
  for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];
  Object.assign(process.env,previous);
  rmSync(root,{recursive:true,force:true});
 }
});
