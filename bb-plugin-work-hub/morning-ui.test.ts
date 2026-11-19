import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,readdirSync,symlinkSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {JSDOM} from 'jsdom';
import {createFakePluginHost,makeThreadResponse} from '@get-bb/plugin-sdk/testing';
import plugin from './server';

test('Start morning runs once, displays evidence, and saves only the edited plan',async()=>{
 const root=mkdtempSync(join(tmpdir(),'morning-fixture-'));
 const dom=new JSDOM('<html><body></body></html>',{url:'https://example.test/',pretendToBeVisual:true});
 Object.defineProperty(globalThis,'React',{value:await import('react'),configurable:true});
 const {window}=dom;
 for(const key of ['window','document','MutationObserver','Element','HTMLElement','Node','NodeFilter','HTMLInputElement','HTMLTextAreaElement','CustomEvent','Event','MouseEvent','getComputedStyle','requestAnimationFrame','cancelAnimationFrame','localStorage','navigator']){
  Object.defineProperty(globalThis,key,{configurable:true,value:key==='window'?window:(window as any)[key]});
 }
 Object.defineProperty(window,'matchMedia',{value:()=>({matches:false,addEventListener(){},removeEventListener(){}})});
 globalThis.ResizeObserver=class {observe(){} unobserve(){} disconnect(){}} as typeof ResizeObserver;
 const {loadPluginApp,renderSlot}=await import('@get-bb/plugin-sdk/testing/app');
 const {fireEvent,screen,waitFor,within}=await import('@testing-library/react');
 const old={...process.env};
 let host:ReturnType<typeof createFakePluginHost>|undefined;
 let slot:ReturnType<typeof renderSlot>|undefined;
 try{
  for(const key of Object.keys(process.env))if(key.startsWith('WORK_HUB_'))delete process.env[key];
  mkdirSync(join(root,'.state'));
  const cfg=join(root,'config.json');
  writeFileSync(cfg,JSON.stringify({config:{sources:[]},bb_context:{project:{id:'proj_fixture'}}}),{mode:0o600});
  process.env.WORK_HUB_STORAGE='json';process.env.WORK_HUB_CONFIG_FILE=cfg;
  const hub=resolve(import.meta.dirname,'../hub'),python=resolve(hub,'../.venv/bin/python');
  for(const file of readdirSync(hub))if(file.endsWith('.py'))symlinkSync(join(hub,file),join(root,file));
  const seed=`from store import Store,digest
import sys
s=Store(sys.argv[1])
s.ingest('bb',[dict(external_id='thr_fixture',url='bb-thread:thr_fixture',title='Release checklist',text='Synthetic return point')])
wid=digest('bb','thr_fixture')
s.mutate('work',wid,'focus')
s.save_intent(wid,0,'Review release checklist')
s.save_day_plan(s.day_plan()['date'],0,[],'Retain this note')
print(wid)
s.close()`;
  const wid=execFileSync(python,['-c',seed,join(root,'.state/work.db')],{cwd:root,encoding:'utf8'}).trim();
  const thread=makeThreadResponse({id:'thr_morning',archivedAt:null,deletedAt:null});
  let meta={},spawns=0;
  host=createFakePluginHost({pluginId:'work-hub',settings:{hubPath:root,pythonPath:python,collectThreads:true,storageFile:cfg,storageBackend:'json'},sdk:{threads:{list:async()=>[],get:async()=>thread,getPluginMetadata:async()=>meta,spawn:async args=>{
    spawns++;meta=args.pluginMetadata??{};assert.equal(args.projectId,'proj_fixture');assert.match(String(args.prompt),/one question at a time/);
    assert.match(String(args.prompt),/work_hub_morning_question/);
    return thread;
  }}}});
  await plugin(host.bb);
  const call=(name:string,input:unknown)=>host!.harness.behavior.callRpc(name,input) as Promise<any>;
  const names=['snapshot','workDetail','workIndex','startMorning','morningStatus','saveDayPlan'];
  const app=await loadPluginApp(()=>import('./app'));
  slot=renderSlot(app.navPanels[0],{subPath:''},{rpc:Object.fromEntries(names.map(name=>[name,(args:unknown)=>call(name,args??null)]))});
  const start=await screen.findByRole('button',{name:'Start morning'});
  await waitFor(()=>assert.equal((start as HTMLButtonElement).disabled,false));
  fireEvent.click(start);
  const repeated=Promise.all([call('startMorning',{}),call('startMorning',{})]);
  await waitFor(()=>assert.equal(slot!.inspection.navigateCalls.length,1));
  assert.equal(screen.queryByRole('dialog'),null);
  assert.ok(JSON.stringify(slot.inspection.navigateCalls).includes(thread.id));
  assert.ok((await repeated).every(r=>r.threadId===thread.id));
  const saved=await call('snapshot',null);
  assert.deepEqual(saved.today.work_ids,[]);
  assert.deepEqual(await call('startMorning',{}),{threadId:thread.id});assert.equal(spawns,1);
  const tool=(name:string,args:unknown)=>host!.harness.behavior.callAgentTool(name,args,{threadId:thread.id});
  await tool('work_hub_morning',{wait:true});
  const first=await call('morningStatus',null);
  assert.equal(first.finished,true);
  assert.ok(first.phases.find((p:any)=>p.key==='slack').sections.some((s:any)=>s.state==='skipped'));
  assert.ok(first.phases[0].sections[0].items.some((i:any)=>i.title==='Review release checklist'));
  const args={question:'Which plan should we use?',choices:['Save this plan','Keep current plan']};
  const pending=tool('work_hub_morning_question',args);
  const pendingCard=async()=>{
   await waitFor(()=>assert.equal(host!.harness.inspection.pendingInteractions.length,1));
   return host!.harness.inspection.pendingInteractions[0];
  };
  const card=await pendingCard();
  const form=renderSlot(app.pendingInteractions.find(r=>r.id===card.rendererId)!,{
   interaction:{...card,createdAt:Date.now(),expiresAt:null,title:'Morning plan'},
   submit:async(value:string)=>host!.harness.behavior.submitInteraction(card.id,value),
   cancel:async()=>host!.harness.behavior.cancelInteraction(card.id),
  });
  try{
   const field=await screen.findByRole('group',{name:args.question});
   assert.equal(within(field).queryByRole('textbox'),null);
   fireEvent.click(within(field).getByRole('button',{name:'Save this plan'}));
   assert.equal(JSON.parse(String(await pending)).choice,'Save this plan');
  }finally{form.lifecycle.unmount()}
  const cancelled=tool('work_hub_morning_question',args);
  host.harness.behavior.cancelInteraction((await pendingCard()).id);
  assert.equal(JSON.parse(String(await cancelled)).outcome,'cancelled');
  const forged=tool('work_hub_morning_question',args);
  host.harness.behavior.submitInteraction((await pendingCard()).id,'Unlisted answer');
  await assert.rejects(forged,/Invalid planning choice/);
  assert.equal((await call('snapshot',null)).today.version,saved.today.version);
  await tool('work_hub_morning_plan',{version:saved.today.version,work_ids:[wid],note:saved.today.note});
  await assert.rejects(()=>tool('work_hub_morning_plan',{version:saved.today.version,work_ids:[],note:'Stale change'}));
  fireEvent.click(screen.getByRole('button',{name:'Edit today’s plan'}));
  const dialog=await screen.findByRole('dialog');
  assert.ok(within(dialog).getByRole('button',{name:'Save today’s plan'}));
  await waitFor(async()=>assert.deepEqual((await call('snapshot',null)).today.work_ids,[wid]));
  assert.equal((await call('snapshot',null)).today.note,'Retain this note');
 }finally{
  slot?.lifecycle.unmount();await host?.harness.lifecycle.dispose();dom.window.close();
  for(const key of Object.keys(process.env))if(key.startsWith('WORK_HUB_'))delete process.env[key];
  Object.assign(process.env,Object.fromEntries(Object.entries(old).filter(([key])=>key.startsWith('WORK_HUB_'))));
  rmSync(root,{recursive:true,force:true});
 }
});
