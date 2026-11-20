import assert from 'node:assert/strict';
import {test} from 'node:test';
import {morningRunner,verifyMorningLinks,type MorningRun} from './morning-run';

test('verify every original link, preserve ordering, and report a failed batch',async()=>{
 const urls=Array.from({length:41},(_,i)=>'https://example.test/'+i);
 const attempted:string[]=[];
 const progress:string[]=[];
 const result=await verifyMorningLinks(async(op,args)=>{
  if(op==='morning_targets')return urls;
  const batch=(args as {urls:string[]}).urls;
  attempted.push(...batch);
  if(batch.includes(urls[16]))throw new Error('private fixture error');
  return {items:batch.map(url=>({url,title:url,detail:'Receipt',work_id:''})),failed:[]};
 },true,sections=>progress.push(sections[0].detail),()=>true);
 assert.deepEqual([...new Set(attempted)].sort(),[...urls].sort());
 assert.equal(attempted.length,45);
 assert.deepEqual(result[0].items.map(item=>item.url),urls);
 assert.equal(result[0].state,'partial');
 assert.match(progress.at(-1)!,/41\/41 originals checked · 37 verified · 4 failed/);
 assert.ok(progress.length>2);
});

test('reuse a finished morning across restart, invalidate changed permissions, and coalesce refresh',async()=>{
 let saved:MorningRun|undefined;
 const runner=()=>morningRunner(async()=>[],async run=>{saved=run});
 const original=runner();
 const first=original.start('2026-06-01','one');
 assert.equal(original.start(first.date,'one').id,first.id);
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(saved?.finished);
 const next=runner();
 next.restore(saved);
 assert.equal(next.start(first.date,'one').id,first.id);
 const refreshed=next.start(first.date,'one',first.id);
 assert.notEqual(refreshed.id,first.id);
 assert.equal(next.start(first.date,'one',first.id).id,refreshed.id);
 assert.notEqual(next.start(first.date,'two').id,refreshed.id);
 next.dispose();
 original.dispose();
});

import {morningDecisions} from './morning-decisions';

test('a large morning queue yields one lead and one review while retaining drill-down',()=>{
 const entries=Array.from({length:150},(_,i)=>({work_id:String(i),title:'Action '+i,detail:'Due today',url:''}));
 const section={label:'Suggested actions',state:'checked' as const,total:150,detail:'',items:entries};
 const run:MorningRun={schemaVersion:1,id:'run',date:'2026-01-01',scope:'fixture',started:0,finished:true,phases:[
  {key:'brief',label:'Brief',state:'finished',sections:[section]},
  {key:'prs',label:'PRs',state:'finished',sections:[{...section,label:'Review candidates',items:entries.slice(0,15)}]},
 ]};
 const brief=morningDecisions(run,[{id:'20',title:'Chosen action'}]);
 assert.equal(brief.startWith?.work_id,'20');
 assert.deepEqual(brief.alternatives.map(e=>e.work_id),['0','1']);
 assert.equal(brief.reviewCandidate?.work_id,'0');
 assert.equal(run.phases[1].sections[0].items.length,15);
});

import {morningThreads} from './morning-thread';
import {createFakePluginHost} from '@get-bb/plugin-sdk/testing';

test('saving the morning plan presents the populated check-in and submits only the reviewed answers',async()=>{
 const date='2026-11-20',threadId='thr_standup';
 const host=createFakePluginHost({pluginId:'work-hub',sdk:{threads:{getPluginMetadata:async()=>({morningDate:date})}}});
 const form={state:'pending',checkin:'fixture',date,url:'',questions:[{id:'q1',type:'text',text:'Today?'}]};
 const calls:string[]=[];
 const api=morningThreads(host.bb,async(op,args)=>{
  calls.push(op);
  if(op==='morning_context')return {date,scope:'fixture',project_id:null,thread_id:threadId,note:'',version:0,work:[]};
  if(op==='standup_form')return form;
  if(op==='save_day_plan')return {version:1};
  assert.equal(op,'submit_standup');assert.deepEqual(args,{reviewed:form,answers:['Edited answer']});
  return {state:'completed'};
 },{start(){throw new Error('Unexpected refresh')}});
 const plan={version:0,work_ids:[],note:'',answers:['Draft answer']};
 const open=async()=>{
  const result=api.save(threadId,plan);
  await new Promise(r=>setImmediate(r));
  const card=host.harness.inspection.pendingInteractions[0];
  assert.equal(card.rendererId,'morning-standup');
  assert.deepEqual(card.payload,{form,draft:{answers:plan.answers}});
  return {result,id:card.id};
 };
 const first=await open();
 await host.harness.behavior.cancelInteraction(first.id);
 assert.deepEqual((await first.result).standup,{state:'deferred',detail:'Standup left pending.'});
 assert.ok(!calls.includes('submit_standup'));
 const second=await open();
 await host.harness.behavior.submitInteraction(second.id,['Edited answer']);
 assert.deepEqual((await second.result).standup,{state:'completed'});
 assert.equal(calls.filter(op=>op==='submit_standup').length,1);
});
