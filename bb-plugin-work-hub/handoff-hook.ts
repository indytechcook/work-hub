import type {BbPluginApi} from '@get-bb/plugin-sdk';
import {z} from 'zod';
type Core=(op:string,args?:unknown)=>Promise<unknown>;
export function registerHandoffs(bb:BbPluginApi,core:Core,capture:(id:string)=>Promise<void>,enabled:boolean){
 if(!enabled)return;
 bb.agents.registerTool({name:'work_hub_checkpoint',description:'Save an immediate agent-reported return point for this linked conversation. Does not resolve commitments or change chosen intent.',instructions:'Before handing work back or pausing, save progress, remaining work, next step, blockers and evidence with work_hub_checkpoint. Report only what happened; distinguish unrun checks and plans. This is a reported checkpoint, not verified completion.',
 parameters:z.object({status:z.enum(['paused','blocked','needs_input','ready_for_review']),progress:z.string().max(6000),remaining:z.string().max(3000).default(''),next_action:z.string().max(1500).default(''),blocker:z.string().max(2000).default(''),evidence:z.array(z.string().max(2000)).max(15).default([])}),
 async execute(args,ctx){
  const history=await bb.sdk.threads.promptHistory({threadId:ctx.threadId,limit:'1'});
  const turn_at=Math.max(0,...history.map(p=>p.createdAt/1000));
  const result=await core('save_handoff',{...args,thread_id:ctx.threadId,turn_at});bb.realtime.publish('changed',{});return JSON.stringify(result);
 }});
 const save=async(id:string)=>{try{await capture(id)}catch{bb.log.warn('Return point capture pending; periodic reconciliation will retry')}};
 bb.events.on('thread.idle',({thread})=>save(thread.id));
 bb.events.on('thread.failed',({thread})=>save(thread.id));
 bb.events.on('thread.active',({thread})=>save(thread.id));
 bb.events.on('interaction.pending',({thread})=>save(thread.id));
}
