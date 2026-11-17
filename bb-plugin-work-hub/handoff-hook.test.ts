import test from 'node:test';
import assert from 'node:assert/strict';
import type {BbPluginApi} from '@get-bb/plugin-sdk';
import {registerHandoffs} from './handoff-hook.ts';
function host(){
 const handlers=new Map<string,(e:any)=>Promise<void>>();const registered:any[]=[];let warnings=0;
 const bb={events:{on:(key:string,fn:any)=>handlers.set(key,fn)},agents:{registerTool:(t:any)=>registered.push(t)},sdk:{threads:{promptHistory:async()=>[{createdAt:12000}]}},realtime:{publish:()=>{}},log:{warn:()=>warnings++}} as unknown as BbPluginApi;
 return {bb,handlers,registered,warnings:()=>warnings};
}
test('capture opt-out installs neither hooks nor checkpoint tool',()=>{
 const h=host();registerHandoffs(h.bb,async()=>null,async()=>{},false);assert.equal(h.handlers.size,0);assert.equal(h.registered.length,0);
});
test('lifecycle transitions capture only the triggering thread and errors are recoverable',async()=>{
 const h=host();const ids:string[]=[];registerHandoffs(h.bb,async()=>null,async id=>{ids.push(id);if(id==='thr_fail')throw new Error('offline')},true);
 for(const key of ['thread.idle','thread.failed','thread.active','interaction.pending'])await h.handlers.get(key)!({thread:{id:'thr_one'}});
 assert.deepEqual(ids,['thr_one','thr_one','thr_one','thr_one']);await h.handlers.get('thread.idle')!({thread:{id:'thr_fail'}});assert.equal(h.warnings(),1);
});
test('checkpoint is scoped to executing thread and takes turn time from host',async()=>{
 const h=host();const calls:any[]=[];registerHandoffs(h.bb,async(op,args)=>{calls.push([op,args]);return args},async()=>{},true);
 const t=h.registered[0],args=t.parameters.parse({status:'paused',progress:'Implemented; tests not run.'});
 await t.execute(args,{threadId:'thr_actual'});assert.equal(calls[0][0],'save_handoff');assert.equal(calls[0][1].thread_id,'thr_actual');assert.equal(calls[0][1].turn_at,12);
 assert.equal(t.parameters.safeParse({status:'fulfilled',progress:'Complete'}).success,false);
});
