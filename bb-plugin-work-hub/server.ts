import {morningThreads} from './morning-thread';
import {morningRun,morningRunner,verifyMorningLinks} from './morning-run';
import {readFile} from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { defineRpcContract, type BbPluginApi } from '@get-bb/plugin-sdk';
import { z } from 'zod';
import {registerHandoffs} from './handoff-hook';
import {registerContextHook} from './context-hook';
import {workRenamer} from './rename-work';
import {workNameError} from './work-name';
import {pinFocusedThreads} from './focus-pins';
import { launchContext } from './launch-context';

const intent=z.object({next_action:z.string(),purpose:z.string(),project:z.string(),thread_id:z.string().nullable(),version:z.number(),updated_at:z.number().nullable()});
const checkpoint=z.object({event_id:z.string(),occurred_at:z.number(),received_at:z.number(),analyzed_at:z.number()});
const handoff=z.object({status:z.string(),progress:z.string(),remaining:z.string(),next_action:z.string(),blocker:z.string(),evidence:z.array(z.string()),at:z.number(),turn_at:z.number(),origin:z.string()});
const conversation = z.object({handoff:handoff.nullable().optional(),role:z.string().optional(),preview:z.object({text:z.string(),author:z.string(),at:z.number(),replies:z.number().nullable()}).nullable().optional(),work_ids:z.array(z.string()),id:z.string(),source:z.string(),external_id:z.string(),title:z.string(),url:z.string(),model:z.string(),updated_at:z.number(),summary:z.string().nullable(),next_action:z.string().nullable(),checkpoint_at:z.number().nullable(),analysis_state:z.string().nullable(),latest_occurred_at:z.number().nullable(),checkpoint:checkpoint.nullable()});
const work = z.object({project:z.string().optional(),id:z.string(),title:z.string(),title_version:z.number().int().nonnegative(),status:z.string(),focus:z.number(),summary:z.string(),next_action:z.string(),updated_at:z.number(),intent,decisions:z.array(z.object({id:z.string(),conversation_id:z.string(),text:z.string(),quote:z.string(),event_id:z.string()})),conversations:z.array(conversation)});
const obligation = z.object({work_ids:z.array(z.string()),id:z.string(),work_id:z.string(),conversation_id:z.string(),title:z.string(),owner:z.string(),direction:z.string(),due_at:z.number().nullable(),state:z.string(),version:z.number(),evidence_quote:z.string(),resolution:z.string().nullable(),updated_at:z.number()});
const attention=z.object({token:z.string(),kind:z.string(),entity_id:z.string(),title:z.string(),reason:z.string(),rank:z.number(),work_ids:z.array(z.string()),source:z.string(),at:z.number(),occurred_at:z.number().nullable(),received_at:z.number().nullable(),seen:z.boolean()});
const dayPlan=z.object({date:z.string(),work_ids:z.array(z.string()),note:z.string(),version:z.number(),updated_at:z.number().nullable()});
const workDetail=z.object({work,obligations:z.array(obligation),now:z.number()});
const workIndex=z.object({items:z.array(z.object({id:z.string(),title:z.string(),status:z.string(),focus:z.number(),project:z.string(),next_action:z.string(),open_count:z.number()})),total:z.number(),has_more:z.boolean()});
const legacySummary=z.object({total:z.number(),due:z.number(),has_more:z.boolean(),teams:z.array(z.string())});
const legacyPage=legacySummary.extend({items:z.array(obligation.extend({source_title:z.string(),url:z.string(),external_id:z.string()}))});
export type LegacyPage=z.infer<typeof legacyPage>;
const snapshotSchema = z.object({legacy:legacySummary.optional(),attention:z.array(attention),attention_total:z.number(),attention_unseen:z.number(),today:dayPlan,previous_day:dayPlan.nullable().optional(),work:z.array(work),obligations:z.array(obligation),sources:z.array(z.object({name:z.string(),state:z.string(),scope:z.string(),checked_at:z.number().nullable(),last_success:z.number().nullable(),error:z.string().nullable()})),changes:z.array(z.object({seq:z.number(),kind:z.string(),entity_id:z.string(),detail:z.string(),at:z.number()})),analysis:z.object({pending:z.number().nullable(),running:z.number().nullable(),errors:z.number().nullable()}),reminders:z.array(z.object({id:z.string(),day:z.string(),state:z.string(),created_at:z.number(),finished_at:z.number().nullable()})),jobs:z.array(z.object({id:z.string(),kind:z.string(),thread_id:z.string().nullable().optional(),source_key:z.string(),revision:z.string(),state:z.string(),detail:z.string(),updated_at:z.number()})),seq:z.number(),now:z.number()});
export type Work=z.infer<typeof work>;
export type Conversation=z.infer<typeof conversation>;
export type Obligation=z.infer<typeof obligation>;
export type Attention=z.infer<typeof attention>;
export type WorkDetail=z.infer<typeof workDetail>;
export type WorkIndex=z.infer<typeof workIndex>;
export type DayPlan=z.infer<typeof dayPlan>;
const workOption=z.object({id:z.string(),title:z.string(),status:z.string()});
const workOptionsSchema=z.object({linked:z.array(workOption),options:z.array(workOption),has_more:z.boolean()});
export type WorkOptions=z.infer<typeof workOptionsSchema>;
const threadContextSchema=z.object({associated_work:z.array(workOption),work:work.nullable(),obligations:z.array(obligation),now:z.number()});
export type ThreadContext=z.infer<typeof threadContextSchema>;
export type Snapshot = z.infer<typeof snapshotSchema>;
const sourceLinkInput=z.object({workId:z.string().min(1).max(100),url:z.string().min(1).max(2048)});
const sourceLinkResult=z.object({state:z.enum(['added','already_linked'])});
const WORK_STATUSES=new Set(['active','waiting','done']);
const mutation = z.object({kind:z.enum(['work','obligation','conversation']),id:z.string().max(100),action:z.enum(['focus','unfocus','active','waiting','done','open','fulfilled','cancelled','link','due']),version:z.number().optional(),value:z.string().optional()});
const reviewRow=z.object({obligation_id:z.string(),title:z.string(),state:z.string(),verdict:z.string().nullable(),reason:z.string().nullable(),error:z.string().nullable(),checked_at:z.number().nullable(),commitment_state:z.string(),current_version:z.number(),obligation_version:z.number().nullable(),applied_version:z.number().nullable(),work_id:z.string(),url:z.string(),evidence:z.array(z.object({event_id:z.string(),url:z.string(),quote:z.string(),occurred_at:z.number()}))});
const reviewPage=z.object({items:z.array(reviewRow),total:z.number(),has_more:z.boolean(),counts:z.record(z.string(),z.number()),budget:z.object({limit:z.number(),used:z.number(),remaining:z.number()})});
export type CompletionPage=z.infer<typeof reviewPage>;
const workRef=z.object({id:z.string(),title:z.string()});
const closeEntry=z.object({work_id:z.string(),title:z.string(),detail:z.string(),url:z.string()});
const closeSection=z.object({items:z.array(closeEntry),total:z.number()});
const closeReport=z.object({
 suggested:z.array(closeEntry),running:closeSection,completed:closeSection,
 date:z.string(),at:z.number(),repaired:z.number(),related_total:z.number(),related:z.array(z.object({work:workRef,target:workRef,url:z.string()})),folded:z.number(),redundant:z.number(),queued:z.number(),
 duplicate_groups:z.number(),duplicates:z.array(z.array(workRef)),carryover:z.array(workRef),return_note:z.string(),
 commitments:z.record(z.string(),z.number()),oldest_overdue:z.array(workRef),relationship_queue:z.record(z.string(),z.number()),
 coverage:z.array(z.object({source:z.string(),state:z.string(),detail:z.string()})),
});
const closeState=z.object({running:z.boolean(),error:z.string(),report:closeReport.nullable(),nightly:z.boolean()});
export type CloseState=z.infer<typeof closeState>;
const createWorkInput = z.object({
 title: z.string().max(1000).refine(value => !workNameError(value), 'Use a single-line work name of 1–200 characters'),
 purpose: z.string().max(3000).optional(),
 requestId: z.string().min(1).max(100).optional(),
});
const createdWork = z.object({id: z.string(), created: z.boolean()});

export const rpcContract = defineRpcContract({
 legacyPage:{input:z.object({offset:z.number().int().min(0).max(100000)}),output:legacyPage},
 keepLegacy:{input:z.object({obligation_id:z.string().max(100),version:z.number().int()}),output:z.object({ok:z.boolean()})},
 completionPage:{input:z.object({offset:z.number().int().min(0).max(100000),verdict:z.string().max(40)}),output:reviewPage},
 queueCompletion:{input:z.object({scope:z.enum(['unseen','open','selected']),ids:z.array(z.string().max(100)).max(100).optional()}),output:z.object({queued:z.number()})},
 resolveCompletion:{input:z.object({obligationId:z.string().max(100)}),output:z.object({ok:z.boolean()})},
 contextPacket:{input:z.object({threadId:z.string().startsWith('thr_').max(100)}),output:z.record(z.string(),z.unknown())},
 snapshot:{input:z.null(),output:snapshotSchema},
 // Accurate counts and short lists for the Chief of Staff's "Your day".
 yourWork:{input:z.null(),output:z.record(z.string(),z.unknown())},
 workDetail:{input:z.object({workId:z.string().max(100)}),output:workDetail},
 workIndex:{input:z.object({query:z.string().max(200),status:z.enum(['all','active','waiting','done']),offset:z.number().int().min(0).max(100000)}),output:workIndex},
 openCloseOut:{input:z.object({projectId:z.string().max(100).optional()}),output:z.object({threadId:z.string()})},
 closeOut:{input:z.null(),output:z.object({started:z.boolean()})},
 closeOutStatus:{input:z.null(),output:closeState},
 createWork:{input:createWorkInput,output:createdWork},
 renameWork:{input:z.object({workId:z.string().max(100),threadId:z.string().startsWith('thr_').max(100).optional(),version:z.number().int().nonnegative(),title:z.string().max(1000).refine(value=>!workNameError(value),'Use a single-line work name of 1–200 characters')}),output:z.object({title:z.string(),title_version:z.number().int().nonnegative(),thread_error:z.string().optional()})},
 saveIntent:{input:z.object({workId:z.string().max(100),version:z.number().int().nonnegative(),next_action:z.string().max(1500),purpose:z.string().max(3000),project:z.string().max(120),thread_id:z.string().nullable()}),output:intent},
 saveDayPlan:{input:z.object({date:z.string(),version:z.number().int().nonnegative(),work_ids:z.array(z.string()).max(12),note:z.string().max(3000)}),output:dayPlan},
 acknowledgeAttention:{input:z.object({tokens:z.array(z.string().length(32)).max(100)}),output:z.object({ok:z.boolean()})},
 workOptions:{input:z.object({conversationId:z.string().max(100),query:z.string().max(200)}),output:workOptionsSchema},
 addSourceLink:{input:sourceLinkInput,output:sourceLinkResult},
 associate:{input:z.object({conversationId:z.string().max(100),workId:z.string().max(100),action:z.enum(['add','remove'])}),output:z.object({ok:z.boolean()})},
 threadContext:{input:z.object({threadId:z.string().startsWith('thr_').max(100),workId:z.string().max(100).optional()}),output:threadContextSchema},
 prepareLaunch:{input:z.object({workId:z.string().max(100),obligationId:z.string().max(100).optional()}),output:z.object({title:z.string(),prompt:z.string(),parentThreadId:z.string().nullable(),projectId:z.string().nullable()})},
 trackThread:{input:z.object({threadId:z.string().max(100)}),output:z.object({ok:z.boolean()})},
 delegateToChiefOfStaff:{input:z.object({workId:z.string().min(1).max(100)}),output:z.object({ok:z.boolean(),message:z.string()})},
 startMorning:{input:z.object({projectId:z.string().max(100).optional()}),output:z.object({threadId:z.string()})},
 morningStatus:{input:z.null(),output:morningRun.nullable()},
 sync:{input:z.null(),output:z.object({ok:z.boolean()})},
 mutate:{input:mutation,output:z.object({ok:z.boolean(),notice:z.string().optional()})},
 acknowledgeChanges:{input:z.object({seqs:z.array(z.number().int().positive()).max(100)}),output:z.object({ok:z.boolean()})},
 acknowledge:{input:z.object({seq:z.number().int().nonnegative()}),output:z.object({ok:z.boolean()})},
});

export default async function plugin(bb:BbPluginApi) {
 const settings=bb.settings.define({
  hubPath:{type:'string',label:'Portable hub directory',default:''},
  autoContext:{type:'boolean',label:'Automatically link new conversations and fetch referenced context',default:false},
  collectThreads:{type:'boolean',label:'Collect recent BB conversations',default:false},
  threadProjects:{type:'string',label:'Only collect conversations from these BB project ids (comma-separated; blank: every project)',default:''},
  includeHiddenThreads:{type:'boolean',label:'Also collect hidden conversations (internal agent threads)',default:false},
  pythonPath:{type:'string',label:'Python executable',default:'python3'},
  storageBackend:{type:'string',label:'Storage backend (bb or json; blank inherits environment)',default:''},
  storageProfile:{type:'string',label:'Encrypted storage profile (blank uses default/environment)',default:''},
  storageFile:{type:'string',label:'Optional absolute JSON override path (contains private data)',default:''},
  nightlyCloseOut:{type:'boolean',label:'Deep close-out nightly at 10 pm in the Work Hub timezone',default:false},
  remindersEnabled:{type:'boolean',label:'Grouped due follow-up reminders (weekdays 9–18, max 3/day)',default:false},
 });
 const config=await settings.get();
 const storageEnv={...process.env};
 for(const [key,value] of Object.entries({WORK_HUB_STORAGE:config.storageBackend,WORK_HUB_PROFILE:config.storageProfile,WORK_HUB_CONFIG_FILE:config.storageFile})){
  if(value && storageEnv[key]===undefined)storageEnv[key]=value;
 }
 const liveChildren = new Set<ReturnType<typeof spawn>>();
 bb.onDispose(()=>{for(const child of liveChildren)child.kill('SIGTERM');});
 async function core(op:string,args:unknown={},timeout=25000):Promise<unknown>{
  if(!config.hubPath.startsWith('/'))throw new Error('Set Work Hub hubPath to your absolute hub directory before use');
  return new Promise((resolve,reject)=>{
   const child=spawn(config.pythonPath,[`${config.hubPath}/cli.py`],{stdio:['pipe','pipe','pipe'],env:{...storageEnv,WORK_HUB_STATE:`${config.hubPath}/.state`}});
   liveChildren.add(child);let out='';let finished=false;
   const timer=setTimeout(()=>child.kill('SIGTERM'),timeout);
   const finish=(err?:Error,result?:unknown)=>{if(finished)return;finished=true;clearTimeout(timer);liveChildren.delete(child);err?reject(err):resolve(result);};
   child.stdout.on('data',(b:Buffer)=>{out+=b.toString();if(out.length>8_000_000){child.kill();finish(new Error('Hub response too large'));}});
   child.stderr.on('data',()=>{}); // Private payloads never go to BB logs.
   child.on('error',()=>finish(new Error('Cannot start local hub')));
   child.stdin.on('error',()=>{});
   child.on('close',()=>{try{const r=JSON.parse(out);if(r.ok)finish(undefined,r.result);else finish(new Error(typeof r.error==='string'?r.error:'Hub operation failed'));}catch{finish(new Error('Local hub unavailable; inspect its private state'));}});
   child.stdin.end(JSON.stringify({op,args}));
  });
 }
 registerContextHook(bb,core,config.autoContext&&config.collectThreads);
 // Which conversations may enter the ledger: the chosen projects only, and hidden ones only when asked.
 const threadProjects=new Set(String(config.threadProjects??'').split(',').map(s=>s.trim()).filter(Boolean));
 const collectable=(t:{projectId:string;visibility?:string|null})=>
  (threadProjects.size===0||threadProjects.has(t.projectId))&&(config.includeHiddenThreads||t.visibility!=='hidden');
 async function trackThread(threadId:string){
  const metadata=await bb.sdk.threads.getPluginMetadata({threadId});
  const parsed=z.object({workId:z.string().max(100)}).safeParse(metadata);
  if(!parsed.success)throw new Error('Thread has no Work Hub link');
  const t=await bb.sdk.threads.get({threadId});
  return z.object({ok:z.boolean()}).parse(await core('register_agent_thread',{work_id:parsed.data.workId,thread_id:t.id,title:t.title??t.titleFallback??t.id,model:t.providerId,occurred_at:t.updatedAt/1000}));
 }
 async function captureThread(threadId:string){
  const t=await bb.sdk.threads.get({threadId});
  if(t.archivedAt||t.deletedAt||!collectable(t))return;
  const link=z.object({workId:z.string().max(100)}).safeParse(await bb.sdk.threads.getPluginMetadata({threadId}));
  if(link.success)await trackThread(threadId);
  const [output,history]=await Promise.all([bb.sdk.threads.output({threadId}),bb.sdk.threads.promptHistory({threadId,limit:'3'})]);
  // Re-read status after I/O so a delayed idle callback cannot win over a new turn.
  const current=await bb.sdk.threads.get({threadId});
  if(current.updatedAt!==t.updatedAt||current.status!==t.status)return;
  const prompts=history.slice().sort((a,b)=>a.createdAt-b.createdAt).map(h=>({at:h.createdAt,text:h.input.filter(p=>p.type==='text').map(p=>p.text).join('\n').slice(-9000)}));
  const body=JSON.stringify({captureVersion:2,coverage:'Latest 3 prompts (last 9000 characters each) and latest assistant output (last 26000 characters); not complete transcript',status:t.status,prompts,assistant:(output.output??'').slice(-26000)});
  const title=t.title??t.titleFallback??t.id;
  await core('ingest',{source:'bb',targeted:true,items:[{external_id:t.id,parent_id:t.parentThreadId??t.sourceThreadId,title,model:t.providerId,url:`bb-thread:${t.id}`,version:createHash('sha256').update(JSON.stringify([title,t.parentThreadId??t.sourceThreadId,body])).digest('hex'),text:body,occurred_at:t.updatedAt/1000}]});
  bb.realtime.publish('changed',{});
 }
 registerHandoffs(bb,core,captureThread,config.collectThreads);
 let syncing:Promise<void>|null=null;
 async function collect():Promise<void>{
  if(!config.collectThreads)return;
  if(syncing)return syncing;
  syncing=(async()=>{
   try{
    let offset=0,complete=true;const items=[];const cutoff=Date.now()-7*86400_000;
    while(offset<1000){
     const page=await bb.sdk.threads.list({limit:100,offset,archived:false,includeHidden:config.includeHiddenThreads});
     const metadata=page.length?(await bb.sdk.threads.experimental_listPluginMetadata({threadIds:page.map(t=>t.id)})).threads:[];
     for(const row of metadata){
      const link=z.object({workId:z.string().max(100)}).safeParse(row.metadata);
      if(link.success){const t=page.find(t=>t.id===row.threadId)!;await core('register_agent_thread',{work_id:link.data.workId,thread_id:t.id,title:t.title??t.titleFallback??t.id,model:t.providerId,occurred_at:t.updatedAt/1000});}
     }
     for(const t of page){
      if(t.updatedAt<cutoff||!collectable(t))continue;
      const [output,history]=await Promise.all([bb.sdk.threads.output({threadId:t.id}),bb.sdk.threads.promptHistory({threadId:t.id,limit:'3'})]);
      const prompts=history.slice().sort((a,b)=>a.createdAt-b.createdAt).map(h=>({at:h.createdAt,text:h.input.filter(p=>p.type==='text').map(p=>p.text).join('\n').slice(-9000)}));
      const body=JSON.stringify({captureVersion:2,coverage:'Latest 3 prompts (last 9000 characters each) and latest assistant output (last 26000 characters); not complete transcript',status:t.status,prompts,assistant:(output.output??'').slice(-26000)});
      const title=t.title??t.titleFallback??t.id;
      items.push({external_id:t.id,parent_id:t.parentThreadId??t.sourceThreadId,title,model:t.providerId,url:`bb-thread:${t.id}`,version:createHash('sha256').update(JSON.stringify([title,t.parentThreadId??t.sourceThreadId,body])).digest('hex'),text:body,occurred_at:t.updatedAt/1000});
     }
     offset+=page.length;if(page.length<100)break;if(offset>=1000)complete=false;
    }
    for(let i=0;i<items.length;i+=500)await core('ingest',{source:'bb',items:items.slice(i,i+500),scope:'Unarchived threads and side conversations updated within 7 days in the selected projects (hidden ones only if enabled); latest 3 prompts and latest assistant output',complete:false});
    await core('ingest',{source:'bb',items:[],checkpoint:Date.now(),scope:'Unarchived threads and side conversations updated within 7 days in the selected projects (hidden ones only if enabled); latest 3 prompts and latest assistant output',complete});
    bb.realtime.publish('changed',{});
   }catch{await core('source_failure',{source:'bb',message:'BB collection failed; next reconciliation will retry. Existing receipts retained.'});}
  })().finally(()=>{syncing=null;});
  return syncing;
 }
 const morning=morningRunner(async (step,update,active)=>{
  if(step==='links')return verifyMorningLinks(core,config.collectThreads,update,active);
  if(step==='bb')await collect();
  // Large Slack histories are deliberately paced by the shared collector.
  // Let it reach its durable checkpoint while the thread shows interim evidence.
  const timeout=['slack','linear','gmail'].includes(step)?45*60_000:180_000;
  return core('morning_step',{step,bb_enabled:config.collectThreads},timeout);
 },async run=>{
  try{await core('save_morning_result',{run})}catch{bb.log.warn('Morning result available in memory; local persistence failed.')}
 });
 let restored:Promise<void>|undefined;
 async function beginMorning(date:string,scope:string,previousId?:string){
  restored??=core('last_morning_result').then(value=>morning.restore(value)).catch(()=>{restored=undefined});
  await restored;
  return morning.start(date,scope+String(config.collectThreads)+':decisions-v1',previousId);
 }
 bb.onDispose(()=>morning.dispose());
 const planning=morningThreads(bb,core,{start:beginMorning});
 const rename=workRenamer({
  detail:async workId=>workDetail.parse(await core('work_detail',{work_id:workId})),
  save:async(workId,version,title)=>z.object({title:z.string(),title_version:z.number().int().nonnegative()}).parse(await core('rename_work',{work_id:workId,version,title})),
  recordThread:(workId,threadId,title)=>core('record_thread_name',{work_id:workId,thread_id:threadId,title}),
  threads:{get:args=>bb.sdk.threads.get(args),update:args=>bb.sdk.threads.update(args)},
  changed:()=>bb.realtime.publish('changed',{}),
 });
 async function addSourceLink(input:z.infer<typeof sourceLinkInput>){
  const {workId,url}=sourceLinkInput.parse(input);
  const result=sourceLinkResult.parse(await core('add_source_link',{work_id:workId,url},100000));
  bb.realtime.publish('changed',{});
  return result;
 }
 async function createWork(input:z.infer<typeof createWorkInput>){
  const {title,purpose,requestId} = createWorkInput.parse(input);
  const saved = createdWork.parse(await core('create_work',{title,purpose,request_id:requestId}));
  if(saved.created)bb.realtime.publish('changed',{});
  return saved;
 }
 bb.agents.registerTool({
  name:'work_hub_watch_reply',description:'Watch the original Slack or Gmail conversation after sending a question.',
  instructions:'After an authorized question is sent on the user’s behalf via Slack or Gmail, register its actual conversation URL and sent Slack timestamp or Gmail message ID. Use a known work ID when available. A new reply is evidence to review; it does not fulfill a commitment.',
  parameters:z.object({url:z.string().max(2000),sent_id:z.string().max(32),work_id:z.string().max(100).optional()}),
  async execute(args){return JSON.stringify(await core('watch_reply',args))}
 });
 bb.agents.registerTool({
  name:'work_hub_create_work',
  description:'Save a standalone personal work item in the local Work Hub. Returns its ID.',
  instructions:'When asked to add personal work to Work Hub, check for existing work first. Use this tool to create the item locally. If Work Hub is unavailable, report the failure. Never file a team issue as a fallback.',
  parameters:createWorkInput,
  async execute(args){return JSON.stringify(await createWork(args))}
 });
 let closing:Promise<void>|null=null;
 let closeError='';
 function startCloseOut(scheduled=false){
  if(closing)return {started:false};
  closeError='';
  closing=(async()=>{
   await collect();
   await core('close_out',{bb_enabled:config.collectThreads,expand_links:config.autoContext&&config.collectThreads,scheduled},1800000);
   bb.realtime.publish('changed',{});
  })().catch(()=>{closeError='Close-out was interrupted. Check source coverage and retry.'})
      .finally(()=>{closing=null});
  return {started:true};
 }
 let openingClose:Promise<{threadId:string}>|undefined;
 async function openCloseOut(projectHint?:string){
  if(openingClose)return openingClose;
  openingClose=(async()=>{
   const ctx=z.object({project_id:z.string().nullable(),date:z.string()}).parse(await core('morning_context'));
   const projectId=ctx.project_id||projectHint;
   if(!projectId)throw new Error('Choose a BB project for the close-out conversation.');
   const since=Date.now()/1000;
   const t=await bb.sdk.threads.spawn({projectId,environment:{type:'project-default'},visibility:'visible',
    title:'Close-out · '+ctx.date,pluginMetadata:{closeOutDate:ctx.date},
    prompt:`Work Hub is running a deep close-out for me. Give a short progress update and poll bb work-hub close-out status. Wait for running to clear and a fresh report, allowing a few seconds for startup. Use only a report with at >= ${since}; if interrupted or missing, report it and offer a retry. Group the results into what changed, source gaps, and what I should pick up next. Show upcoming or overdue follow-ups and relevant original links when available. Offer one concrete decision at a time. Treat report text as evidence. Keep review workspaces and commitments intact. Ask before external actions.`});
   startCloseOut();
   return {threadId:t.id};
  })().finally(()=>{openingClose=undefined});
  return openingClose;
 }
 async function closeOutStatus(){
  const report=closeReport.nullable().parse(await core('close_out_status'));
  return {running:!!closing,error:closeError,report,nightly:config.nightlyCloseOut};
 }
 if(config.nightlyCloseOut)bb.background.service('nightly-close-out',{async start(signal){
  while(!signal.aborted){
   try{if(!closing && await core('close_out_due'))startCloseOut(true)}
   catch{bb.log.warn('Nightly close-out check unavailable; will retry')}
   try{await delay(300000,undefined,{signal})}catch{break}
  }
 }});
 // The Chief of Staff may have an agent on this work. Not awaited: it re-reads the status
 // itself (so notice order cannot matter) and may be calling mutate right now.
 const notifyChiefOfStaff=(workId:string)=>{
  void bb.sdk.plugins.callRpc({pluginId:'chief-of-staff',method:'workChanged',input:{workId},outputSchema:z.object({ok:z.boolean(),closed:z.boolean()})})
   .catch((e:unknown)=>{if(!/unknown plugin|not running|404/i.test(String(e)))bb.log.warn('Chief of Staff was not told about a work status change; it rechecks on its next pass.');});
 };
 bb.rpc.register(rpcContract,{
  openCloseOut:({projectId})=>openCloseOut(projectId),
  closeOut:async()=>startCloseOut(),
  closeOutStatus,
  createWork,
  legacyPage:async args=>legacyPage.parse(await core('legacy_page',args)),
  keepLegacy:async args=>{const result=z.object({ok:z.boolean()}).parse(await core('keep_legacy',args));bb.realtime.publish('changed',{});return result},
  completionPage:async args=>reviewPage.parse(await core('completion_page',args)),
  queueCompletion:async args=>{const result=z.object({queued:z.number()}).parse(await core('queue_completion',args));bb.realtime.publish('changed',{});return result},
  resolveCompletion:async({obligationId})=>{const result=z.object({ok:z.boolean()}).parse(await core('resolve_completion',{obligation_id:obligationId}));bb.realtime.publish('changed',{});return result},
  contextPacket:async({threadId})=>z.record(z.string(),z.unknown()).parse(await core('context_packet',{thread_id:threadId})),
  prepareLaunch:async({workId,obligationId})=>{
   const snapshot=snapshotSchema.parse(await core('snapshot'));
   const detail=workDetail.parse(await core('work_detail',{work_id:workId}));
   const context=launchContext({...snapshot,work:[detail.work],obligations:detail.obligations.filter(o=>['open','needs_review'].includes(o.state))},workId,obligationId);
   if(!context.parentThreadId)return {...context,projectId:null};
   const parent=await bb.sdk.threads.get({threadId:context.parentThreadId});
   return {...context,projectId:parent.projectId};
  },
  // Work Hub collects and stores; the Chief of Staff plugin runs agents. Hand work over to it.
  delegateToChiefOfStaff:async({workId})=>{
   const result=await bb.sdk.plugins.callRpc({pluginId:'chief-of-staff',method:'delegate',input:{workIds:[workId]},outputSchema:z.object({added:z.array(z.object({id:z.string()}).passthrough()),skipped:z.number()})})
    .catch((e:unknown)=>{throw new Error(/unknown plugin|not running|404/i.test(String(e))?'The Chief of Staff plugin is not running.':String(e))});
   bb.realtime.publish('changed',{});
   return result.added.length>0
    ?{ok:true,message:'Handed to the Chief of Staff. Its agent and any questions show in the Chief of Staff panel.'}
    :{ok:false,message:'Not delegated: an agent is already on it, or the work is done (reopen it first).'};
  },
  trackThread:async({threadId})=>{const result=await trackThread(threadId);bb.realtime.publish('changed',{});return result;},
  threadContext:async({threadId,workId})=>threadContextSchema.parse(await core('thread_context',{thread_id:threadId,...(workId?{work_id:workId}:{})})),
  workOptions:async({conversationId,query})=>workOptionsSchema.parse(await core('work_options',{conversation_id:conversationId,query})),
  addSourceLink,
  associate:async({conversationId,workId,action})=>{const result=z.object({ok:z.boolean()}).parse(await core('associate',{conversation_id:conversationId,work_id:workId,action}));bb.realtime.publish('changed',{});return result;},
  workDetail:async({workId})=>workDetail.parse(await core('work_detail',{work_id:workId})),
  workIndex:async args=>workIndex.parse(await core('work_index',args)),
  renameWork:rename,
  saveIntent:async({workId,...args})=>{const result=intent.parse(await core('save_intent',{work_id:workId,...args}));bb.realtime.publish('changed',{});return result;},
  saveDayPlan:async args=>{const result=dayPlan.parse(await core('save_day_plan',args));bb.realtime.publish('changed',{});return result;},
  acknowledgeChanges:async args=>{const result=z.object({ok:z.boolean()}).parse(await core('acknowledge_changes',args));bb.realtime.publish('changed',{});return result;},
  acknowledgeAttention:async args=>{const result=z.object({ok:z.boolean()}).parse(await core('acknowledge_attention',args));bb.realtime.publish('changed',{});return result;},
  snapshot:async()=>snapshotSchema.parse(await core('snapshot')),
  yourWork:async()=>z.record(z.string(),z.unknown()).parse(await core('your_work')),
  startMorning:async({projectId})=>planning.open(projectId),
  morningStatus:async()=>morning.status(),
  sync:async()=>{await collect();return {ok:true};},
  mutate:async args=>{
   const result=await core('mutate',args);
   bb.realtime.publish('changed',{});
   if(args.kind==='work' && WORK_STATUSES.has(args.action))notifyChiefOfStaff(args.id);
   if(args.kind==='work' && args.action==='focus'){
    const {thread_ids}=z.object({thread_ids:z.array(z.string())}).parse(result);
    return {ok:true,notice:await pinFocusedThreads(thread_ids,bb.sdk.threads)};
   }
   return {ok:true};
  },
  acknowledge:async(args)=>{await core('acknowledge',args);bb.realtime.publish('changed',{});return {ok:true};},
 });
 bb.cli.register({name:'work-hub',summary:'Read the portable work register and source coverage',commands:[{name:'health-check',summary:'Check persistent source and analyzer failures',usage:'bb work-hub health-check'},{name:'watch-reply',summary:'Track replies to a sent question',usage:'bb work-hub watch-reply <url> <sent-message-id> [work-id]'},{name:'reply-check',summary:'Check due reply watches',usage:'bb work-hub reply-check'},{name:'dispatch',summary:'Launch the next eligible private agent',usage:'bb work-hub dispatch [pilot-PR-url]'},{name:'review-workflow',summary:'Print the private review workflow',usage:'bb work-hub review-workflow'},{name:'review-workflow-help',summary:'Show how to start the private review workflow',usage:'bb work-hub review-workflow-help'},{name:'close-out',summary:'Run deep local reconciliation or read its last report',usage:'bb work-hub close-out [run|status]'},{name:'create',summary:'Create a standalone personal work item',usage:'bb work-hub create <title> [purpose]'},{name:'morning',summary:'Run or inspect the guided checks from today’s morning thread',usage:'bb work-hub morning [--wait] [--refresh RUN_ID] [phase] [offset] | morning ask <question> <choice> <choice> [...] | morning save <JSON> | morning standup-review <answers-JSON>'},{name:'add-link',summary:'Attach source URLs to a work item',usage:'bb work-hub add-link <work-id> <url> [url...]'},{name:'status',summary:'Work and coverage as JSON',usage:'bb work-hub status'},{name:'sync',summary:'Reconcile recent BB conversations',usage:'bb work-hub sync'}],async run(argv,ctx){
  if(argv[0]==='review-workflow'&&argv.length===1)return {exitCode:0,stdout:await readFile(config.hubPath+'/private-review.js','utf8')};
  if(argv[0]==='review-workflow-help')return {exitCode:0,stdout:'From the visible review thread, verify its PR workspace. Use bb_workflow_run with the exact script from bb work-hub review-workflow and args {url,base,head}. For CLI-only sessions pass the verified URL and full commits to bb workflows run --script "$(bb work-hub review-workflow)" --args \'{"url":"PR_URL","base":"BASE_SHA","head":"HEAD_SHA"}\'. Read the result and link both reports here. Keep this workspace.'};
  if(argv[0]==='health-check'&&argv.length===1){
   const changes=z.array(z.string()).parse(await core('watch_health',{bb_enabled:config.collectThreads}));
   if(changes.length)bb.realtime.publish('changed',{});
   return {exitCode:0,stdout:changes.join(', ')};
  }
  if(argv[0]==='watch-reply'&&(argv.length===3||argv.length===4)){
   const response=await core('watch_reply',{url:argv[1],sent_id:argv[2],work_id:argv[3]});
   return {exitCode:0,stdout:JSON.stringify(response)};
  }
  if(argv[0]==='reply-check'&&argv.length===1){
   const result=z.object({arrivals:z.number(),errors:z.number()}).parse(await core('watch_replies',{},240000));
   if(result.arrivals)bb.realtime.publish('changed',{});
   return {exitCode:result.errors?1:0,stdout:result.errors?'A watched source could not be read; queued for retry.':result.arrivals?String(result.arrivals)+(result.arrivals===1?' new reply':' new replies'):''};
  }
  if(argv[0]==='dispatch'&&(argv.length===1||argv.length===2)){
   const result=z.object({message:z.string()}).parse(await core('dispatch_jobs',{pilot:argv[1],parent:argv[1]?ctx.threadId:undefined},420000));
   if(result.message)bb.realtime.publish('changed',{});
   return {exitCode:0,stdout:result.message};
  }
  if(argv[0]==='morning'){
   const threadId=ctx.threadId;
   if(!threadId)return {exitCode:1,stdout:'Run from today’s morning thread with the work_hub_morning tool.'};
   if(argv[1]==='save'){
    if(argv.length!==3)throw new Error('Pass the confirmed plan and optional standup answers as one JSON argument.');
    return {exitCode:0,stdout:JSON.stringify(await planning.save(threadId,JSON.parse(argv[2]),ctx.signal))};
   }
   if(argv[1]==='standup-review'){
    if(argv.length!==3)throw new Error('Pass the JSON array of draft answers as one argument.');
    return {exitCode:0,stdout:JSON.stringify(await planning.standup(threadId,{answers:JSON.parse(argv[2])},ctx.signal))};
   }
   if(argv[1]==='ask'){
    const [,,question,...choices]=argv;
    return {exitCode:0,stdout:JSON.stringify(await planning.ask(threadId,{question,choices},ctx.signal))};
   }
   const rest=argv.slice(1).filter(arg=>arg!=='--wait');
   const flag=rest.indexOf('--refresh');
   const refreshRunId=flag<0?undefined:z.string().uuid().parse(rest.splice(flag,2)[1]);
   const [phase,start]=rest;
   const offset=start===undefined?0:z.coerce.number().int().nonnegative().parse(start);
   return {exitCode:0,stdout:JSON.stringify(await planning.report(threadId,{wait:argv.includes('--wait'),refreshRunId,phase,offset}))};
  }
  if(argv[0]==='close-out'){
   if(argv.length===1)return {exitCode:0,stdout:JSON.stringify(await openCloseOut(ctx.projectId??undefined))};
   if(argv.length===2&&argv[1]==='run')return {exitCode:0,stdout:JSON.stringify(startCloseOut())};
   if(argv.length===2&&argv[1]==='status')return {exitCode:0,stdout:JSON.stringify(await closeOutStatus())};
   return {exitCode:1,stdout:'Usage: bb work-hub close-out [run|status]'};
  }
  if(argv[0]==='create'){
   if(argv.length<2||argv.length>3)return {exitCode:1,stdout:'Usage: bb work-hub create <title> [purpose]'};
   return {exitCode:0,stdout:JSON.stringify(await createWork({title:argv[1],purpose:argv[2]}))};
  }
  if(argv[0]==='add-link'){
   const [,workId,...urls]=argv;
   if(!workId||!urls.length||urls.length>20)return {exitCode:1,stdout:'Usage: bb work-hub add-link <work-id> <url> [url...] (up to 20 links)'};
   const lines:string[]=[];
   let failed=false;
   for(const url of urls){
    try{const result=await addSourceLink({workId,url});lines.push(result.state==='added'?'Added':'Already linked');}
    catch(error){failed=true;lines.push(error instanceof Error?error.message:'Could not attach link');}
   }
   return {exitCode:failed?1:0,stdout:lines.map((message,i)=>`Link ${i+1}: ${message}`).join('\n')};
  }
  if(argv[0]==='sync'){await collect();return {exitCode:0,stdout:'BB collection attempted; inspect source status for coverage.'};}
  if(argv[0]==='status')return {exitCode:0,stdout:JSON.stringify(snapshotSchema.parse(await core('snapshot')))};
  return {exitCode:0,stdout:'bb work-hub status | sync | close-out [run|status] | create <title> [purpose] | add-link <work-id> <url> [url...]'};
 }});

 async function remind(){
  if(!config.remindersEnabled)return;
  const claim=z.object({id:z.string()}).nullable().parse(await core('claim_reminder'));
  if(!claim)return;
  const payload=z.object({count:z.number()}).nullable().parse(await core('reminder_payload',{id:claim.id}));
  if(!payload)return;
  let delivered=false;
  try{
   const result=await bb.sdk.plugins.callRpc({pluginId:'push-notifications',method:'notifications.send',input:{title:'Work Hub · follow-ups due',body:`${payload.count} confirmed follow-up${payload.count===1?' is':'s are'} due. Open Work Hub to review.`},outputSchema:z.object({ok:z.literal(true),channels:z.array(z.enum(['web','desktop','mobile']))})});
   delivered=result.channels.length>0;
  }finally{await core('finish_reminder',{id:claim.id,delivered});bb.realtime.publish('changed',{});}
 }
 bb.background.service('due-reminders',{async start(signal){while(!signal.aborted){try{await remind();}catch{bb.log.warn('Reminder delivery uncertain; ledger retained for review');}try{await delay(60000,undefined,{signal});}catch{break;}}}});
 bb.background.service('collect-bb',{async start(signal){while(!signal.aborted){await collect();try{await delay(120000,undefined,{signal});}catch{break;}}}});
}
