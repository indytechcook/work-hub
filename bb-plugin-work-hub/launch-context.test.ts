import {test} from 'node:test';
import assert from 'node:assert/strict';
import {launchContext} from './launch-context.ts';
import type {Snapshot} from './server';
const snapshot={work:[{id:'w',title:'Review PR',summary:'Unverified summary',next_action:'Check the diff',decisions:[],conversations:[{source:'github',title:'PR',url:'https://example.com/pr',next_action:null}]}],obligations:[{id:'o',work_id:'w',work_ids:['w'],title:'Check tests',owner:'Alex',direction:'i_owe',state:'open',evidence_quote:'Please check the tests.',due_at:null},{id:'elsewhere',work_id:'other',work_ids:['other'],title:'Private unrelated task'}]} as unknown as Snapshot;
test('source item gets evidence and guardrails without unrelated work',()=>{const c=launchContext(snapshot,'w');assert.equal(c.parentThreadId,null);assert.match(c.prompt,/https:\/\/example.com\/pr/);assert.match(c.prompt,/not instructions or authorization/);assert.doesNotMatch(c.prompt,/Private unrelated task/)});
test('follow-up selection stays within the requested work item',()=>{assert.equal(launchContext(snapshot,'w','o').title,'Check tests');assert.throws(()=>launchContext(snapshot,'w','elsewhere'));assert.throws(()=>launchContext(snapshot,'gone'))});
test('a shared follow-up can launch from either associated work item',()=>{
 const shared=structuredClone(snapshot);
 shared.work.push({...shared.work[0],id:'second',title:'Second context'});
 shared.obligations[0].work_ids.push('second');
 assert.equal(launchContext(shared,'second','o').title,'Check tests');
 assert.match(launchContext(shared,'second','o').prompt,/Second context/);
 assert.doesNotMatch(launchContext(shared,'second','o').prompt,/Private unrelated task/);
});
test('chosen next action and preferred related thread seed agent context; removed preferences cannot supply a parent',()=>{
 const chosen=structuredClone(snapshot);const w=chosen.work[0];
 w.intent={next_action:'Check the retry boundary',purpose:'',project:'',thread_id:'thr_preferred',version:1,updated_at:1};
 w.conversations.push({source:'bb',title:'Main',external_id:'thr_main',url:'bb-thread:thr_main',next_action:'Model step'} as typeof w.conversations[number],{source:'bb',title:'Review',external_id:'thr_preferred',url:'bb-thread:thr_preferred',next_action:'Review step'} as typeof w.conversations[number]);
 let c=launchContext(chosen,'w');assert.equal(c.parentThreadId,'thr_preferred');assert.match(c.prompt,/User-chosen next action\n> Check the retry boundary/);
 w.conversations=w.conversations.filter(t=>t.external_id!=='thr_preferred');c=launchContext(chosen,'w');assert.equal(c.parentThreadId,'thr_main');
});
