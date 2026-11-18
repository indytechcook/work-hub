import {test} from 'node:test';
import assert from 'node:assert/strict';
import {workRenamer} from './rename-work.ts';
function fixture(){
 const state={version:0,title:'Work',thread:'Thread',linked:true,deleted:false,updates:0,records:0,changes:0,failUpdate:false,failRecord:false};
 const deps={
  detail:async()=>({work:{conversations:state.linked?[{source:'bb',external_id:'thr_one'}]:[]}}),
  save:async(_:string,version:number,title:string)=>{if(version!==state.version)throw Error('Stale');state.title=title;return {title,title_version:++state.version}},
  recordThread:async()=>{if(state.failRecord)throw Error('Ledger unavailable');state.records++},
  threads:{get:async()=>({title:state.thread,deletedAt:state.deleted?1:null}),update:async({title}:{title:string})=>{state.updates++;if(state.failUpdate)throw Error('Offline');state.thread=title;return {title}}},
  changed:()=>{state.changes++},
 };
 return {state,deps,rename:workRenamer(deps)};
}
const input={workId:'w',version:0,title:'New name',threadId:'thr_one'};
test('work-only rename never updates a thread',async()=>{const f=fixture();await f.rename({...input,threadId:undefined});assert.equal(f.state.updates,0);assert.equal(f.state.records,0)});
test('unlinked/deleted threads and stale work edits cannot rename threads',async()=>{
 for(const kind of ['linked','deleted','version']){
  const f=fixture();if(kind==='linked')f.state.linked=false;if(kind==='deleted')f.state.deleted=true;if(kind==='version')f.state.version=1;
  await assert.rejects(f.rename(input));assert.equal(f.state.updates,0);assert.equal(f.state.title,'Work');
 }
});
test('thread failure returns saved version and can be retried',async()=>{
 const f=fixture();f.state.failUpdate=true;const saved=await f.rename(input);
 assert.equal(saved.title_version,1);assert.match(saved.thread_error!,/Work name saved/);assert.equal(f.state.records,0);
 f.state.failUpdate=false;assert.equal((await f.rename({...input,version:1})).thread_error,undefined);assert.equal(f.state.thread,'New name');
});
test('projection failure reports both saved and retry skips duplicate BB update',async()=>{
 const f=fixture();f.state.failRecord=true;const saved=await f.rename(input);
 assert.match(saved.thread_error!,/Both names saved/);f.state.failRecord=false;
 await f.rename({...input,version:saved.title_version});assert.equal(f.state.updates,1);assert.equal(f.state.records,1);
});
test('association removed during save prevents BB mutation',async()=>{
 const f=fixture();const save=f.deps.save;f.deps.save=async(...args)=>{f.state.linked=false;return save(...args)};
 const result=await workRenamer(f.deps)(input);assert.match(result.thread_error!,/Work name saved/);assert.equal(f.state.updates,0);
});
test('overlapping saves cannot reorder BB thread writes',async()=>{
 const f=fixture();let resume!:()=>void;
 const gate=new Promise<void>(resolve=>{resume=resolve});const update=f.deps.threads.update;
 f.deps.threads.update=async args=>{await gate;return update(args)};const rename=workRenamer(f.deps);
 const first=rename(input);
 await assert.rejects(rename({...input,version:1,title:'Later'}),/already in progress/);
 resume();await first;
 await rename({...input,version:1,title:'Later'});assert.equal(f.state.thread,'Later');
});
