import {randomUUID} from 'node:crypto';
import {z} from 'zod';

export const morningEntry=z.object({title:z.string(),detail:z.string(),url:z.string(),work_id:z.string()});
const section=z.object({label:z.string(),state:z.enum(['checked','skipped','partial','failed']),detail:z.string(),total:z.number(),items:z.array(morningEntry)});
const phase=z.object({key:z.string(),label:z.string(),state:z.enum(['pending','running','finished']),sections:z.array(section)});
export const morningRun=z.object({schemaVersion:z.literal(1),id:z.string(),date:z.string(),scope:z.string(),finished:z.boolean(),started:z.number(),phases:z.array(phase)});
export type MorningRun=z.infer<typeof morningRun>;
const steps=[
 ['brief','Review saved work and coverage'],['checkout','Check main checkout and worktrees'],['projects','Check projects and deadlines'],
 ['cadence','Check recurring work and constraints'],['bb','Refresh BB conversations'],['calendar','Check calendar'],['linear','Refresh assigned issues'],['github','Refresh open PR receipts'],
 ['slack','Check Slack conversations'],['gmail','Check mail and waiting threads'],['drive','Check changed files'],
 ['meetings','Check meeting notes'],['betterstack','Check error patterns'],['inbox','Read notifications'],
 ['prs','Check review requests, feedback and CI'],['alerts','Check account alerts'],['links','Verify linked work and waiting threads'],
 ['standup','Prepare weekday standup'],
];

type Section=z.infer<typeof section>;
export type UpdateMorningProgress=(sections:Section[])=>void;
export function morningRunner(read:(key:string,update:UpdateMorningProgress,active:()=>boolean)=>Promise<unknown>,save:(run:MorningRun)=>Promise<void>) {
 let current:MorningRun|null=null;
 let disposed=false;
 async function check(run:MorningRun,key:string){
  if(disposed||run!==current)return;
  const p=run.phases.find(p=>p.key===key)!;
  p.state='running';
  try{
   p.sections=z.array(section).parse(await read(key,sections=>{p.sections=sections},()=>!disposed&&current===run));
  }catch(error){
   // The Python CLI only exposes a sanitized diagnostic. Never publish raw subprocess output.
   const detail=error instanceof Error&&/^Morning check unavailable \([A-Za-z]+\)\./.test(error.message)?error.message:'Check could not finish. Verify source access and retry.';
   p.sections=[{label:p.label,state:'failed',detail,total:0,items:[]}];
  }
  p.state='finished';
 }
 async function execute(run:MorningRun){
  try{
   await check(run,'brief');
   await Promise.all(['checkout','projects','cadence'].map(k=>check(run,k)));
   const sources=['bb','calendar','linear','github','slack','gmail','drive','meetings','betterstack','inbox','prs','alerts'];
   // Each source has its own checkpoint and lock. Bound competing requests to the local host.
   await Promise.all(Array.from({length:3},async()=>{
    while(!disposed&&current===run&&sources.length)await check(run,sources.shift()!);
   }));
   await check(run,'links');
   await check(run,'brief');
   await check(run,'standup');
  }finally{
   run.finished=true;
   if(!disposed&&current===run)await save(run);
  }
 }
 return {
  restore(value:unknown){
   const saved=morningRun.safeParse(value);
   if(!current&&saved.success&&saved.data.finished&&steps.every(([key])=>saved.data.phases.some(p=>p.key===key)))current=saved.data;
  },
  start(date:string,scope:string,previousId?:string){
   if(current&&current.date===date&&current.scope===scope&&(!current.finished||current.id!==previousId))return current;
   current={schemaVersion:1,id:randomUUID(),date,scope,finished:false,started:Date.now(),phases:steps.map(([key,label])=>({key,label,state:'pending',sections:[]}))};
   void execute(current);
   return current;
  },
  status(){return current},
  dispose(){disposed=true},
 };
}


// Walk the original evidence in bounded batches. Keep display order independent
// of network completion order and retain failures alongside successful reads.
export async function verifyMorningLinks(
 core:(op:string,args:unknown,timeout?:number)=>Promise<unknown>,
 bbEnabled:boolean,update:UpdateMorningProgress,active:()=>boolean,
){
 const urls=z.array(z.string()).parse(await core('morning_targets',{bb_enabled:bbEnabled}));
 const batchSchema=z.object({items:z.array(morningEntry),failed:z.array(z.number().int().nonnegative())});
 const entries=new Array<z.infer<typeof morningEntry>>(urls.length);
 const failed=new Set<number>();
 let next=0,checked=0,retried=0,retryTotal=0;
 function report():Section[]{
  const retries=retryTotal?` Retried ${retried}/${retryTotal} originally failed reads.`:'';
  return [{label:'Linked work and waiting threads',state:failed.size||checked<urls.length?'partial':'checked',
   detail:`${checked}/${urls.length} originals checked · ${checked-failed.size} verified · ${failed.size} failed.${retries} Gmail follow-ups use the mail collector.`,
   total:urls.length,items:entries.filter(Boolean)}];
 }
 async function readBatch(indices:number[],retry:boolean){
  const batch=indices.map(i=>urls[i]);
  try{
   const result=batchSchema.parse(await core('morning_links',{urls:batch,retry_errors:retry},360000));
   if(result.items.length!==batch.length||result.failed.some(i=>i>=batch.length))throw new Error('Incomplete original reference batch');
   result.items.forEach((value,i)=>{
    const index=indices[i];
    entries[index]=value;
    if(result.failed.includes(i))failed.add(index);else failed.delete(index);
   });
  }catch{
   indices.forEach(i=>{entries[i]={title:urls[i],url:urls[i],detail:'Could not verify this source.',work_id:''};failed.add(i)});
  }
  if(retry)retried+=batch.length;else checked+=batch.length;
  update(report());
 }
 update(report());
 await Promise.all(Array.from({length:3},async()=>{
  while(active()&&next<urls.length){
   const start=next;
   next+=4;
   await readBatch(urls.slice(start,next).map((_,i)=>start+i),false);
  }
 }));
 const retry=[...failed].sort((a,b)=>a-b);
 retryTotal=retry.length;
 // A single worker rechecks failed reads once, preserving the collector checkpoints.
 for(let i=0;active()&&i<retry.length;i+=4)await readBatch(retry.slice(i,i+4),true);
 return report();
}
