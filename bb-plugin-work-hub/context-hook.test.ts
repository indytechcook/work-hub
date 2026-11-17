import test from 'node:test';
import assert from 'node:assert/strict';
import {createFakePluginHost,makeMessageDispatchHookContext,makeThreadResponse} from '@get-bb/plugin-sdk/testing';
import {registerContextHook} from './context-hook.ts';
const context=()=>makeMessageDispatchHookContext({thread:makeThreadResponse({id:'thr_example',providerId:'codex'}),input:{text:'Work on https://example.slack.com/archives/C123/p1794900000000000',blocks:[]},initiator:'user'});
const packet={thread_id:'thr_example',work:{id:'work',title:'Example',next_action:'Read request'},requests:[]};

test('disabled opt-in never registers a dispatch hook',async()=>{
 const {bb,harness}=createFakePluginHost({pluginId:'work-hub'});
 registerContextHook(bb,async()=>{throw new Error('Must not run')},false);
 assert.equal(harness.inspection.registrations.hooks['message.dispatch'],null);
 await harness.lifecycle.dispose();
});
test('a first turn gets its context before it proceeds',async()=>{
 const {bb,harness}=createFakePluginHost({pluginId:'work-hub',sdk:{threads:{getPluginMetadata:async()=>null}}});
 const calls:string[]=[];registerContextHook(bb,async(op)=>{calls.push(op);return packet},true);
 const decision=await harness.inspection.registrations.hooks['message.dispatch']!(context());
 assert.equal(decision.action,'proceed');assert.deepEqual(calls,['ensure_context']);
 assert.match(harness.inspection.registrations.instructionProvider!({threadId:'thr_example',projectId:'proj_test'})!,/Read request/);
 await harness.lifecycle.dispose();
});
test('slow reads queue only for a bounded interval and retries do not duplicate hydration',async()=>{
 const {bb,harness}=createFakePluginHost({pluginId:'work-hub',sdk:{threads:{getPluginMetadata:async()=>null}}});
 let hydrated=0;let finish!:(v:unknown)=>void;
 registerContextHook(bb,async(op)=>{if(op==='hydrate_context'){hydrated++;return new Promise(resolve=>{finish=resolve})}return {...packet,requests:[{state:'pending'}]}},true);
 const hook=harness.inspection.registrations.hooks['message.dispatch']!;
 const first=await hook(context());assert.equal(first.action,'wait');await hook(context());assert.equal(hydrated,1);
 const original=Date.now;Date.now=()=>original()+13000;
 try{assert.equal((await hook(context())).action,'proceed')}finally{Date.now=original}
 finish(packet);await new Promise(resolve=>setTimeout(resolve,0));await harness.lifecycle.dispose();
});
test('hub failure never blocks conversation indefinitely',async()=>{
 const {bb,harness}=createFakePluginHost({pluginId:'work-hub',sdk:{threads:{getPluginMetadata:async()=>null}}});
 registerContextHook(bb,async()=>{throw new Error('offline')},true);
 assert.equal((await harness.inspection.registrations.hooks['message.dispatch']!(context())).action,'proceed');
 assert.match(harness.inspection.registrations.instructionProvider!({threadId:'thr_example',projectId:'proj_test'})!,/unavailable/);
 await harness.lifecycle.dispose();
});
test('explicit composer work metadata is registered before automatic matching',async()=>{
 const {bb,harness}=createFakePluginHost({pluginId:'work-hub',sdk:{threads:{getPluginMetadata:async()=>({workId:'chosen'})}}});
 const calls:string[]=[];registerContextHook(bb,async(op)=>{calls.push(op);return packet},true);
 await harness.inspection.registrations.hooks['message.dispatch']!(context());
 assert.deepEqual(calls,['register_agent_thread','ensure_context']);await harness.lifecycle.dispose();
});
