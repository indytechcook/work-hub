import {useEffect,useState} from 'react';
import {useRpc} from '@get-bb/plugin-sdk/app';
import {Button} from '@/components/ui/button';
import type {CloseState,rpcContract} from './server';
import {Modal,when} from './work-ui';

export function CloseOut({onClose,onWork,onNote}:{onClose:()=>void;onWork:(id:string)=>void;onNote:()=>void}){
 const rpc=useRpc<typeof rpcContract>();
 const [state,setState]=useState<CloseState|null>(null);
 const [error,setError]=useState('');
 const [revision,refresh]=useState(0);
 const [starting,setStarting]=useState(false);
 useEffect(()=>{
  let alive=true;
  async function load(){
   try{const result=await rpc.call('closeOutStatus',null);if(alive){setState(result);setError('')}}
   catch(e){if(alive)setError(String(e))}
  }
  void load();
  const timer=setInterval(load,4000);
  return ()=>{alive=false;clearInterval(timer)};
 },[rpc,revision]);
 async function start(){
  setStarting(true);
  try{await rpc.call('closeOut',null);refresh(v=>v+1)}
  catch(e){setError(String(e))}
  finally{setStarting(false)}
 }
 const report=state?.report;
 const work=(w:{id:string;title:string})=><button className="wh-link" onClick={()=>onWork(w.id)}>{w.title}</button>;
 return <Modal title="Deep close-out" description="Sync sources, reconcile work and review what carries over." onClose={onClose}>
  <div className="wh-actions"><Button disabled={!state||starting||state.running} onClick={start}>{state?.running?'Close-out running…':'Run close-out'}</Button>
  <Button variant="outline" onClick={onNote}>Edit return note</Button></div>
  {state&&<p className="wh-meta">{state.nightly?'Nightly at 10 pm in your Work Hub timezone':'On demand'}</p>}
  {(error||state?.error)&&<p role="alert">{error||state?.error}</p>}
  {report&&<>
   <h3>Last close-out · {report.date}</h3><p>{when(report.at)} · {report.repaired} source indexes repaired · {report.folded} duplicate cards consolidated · {report.redundant} redundant associations removed · {report.queued} related reads queued.</p>
   <details open><summary>Source sync</summary>{report.coverage.map(s=><p key={s.source}><strong>{s.source} · {s.state}</strong> {s.detail}</p>)}</details>
   <details><summary>Possible duplicates · {report.duplicate_groups}</summary>{report.duplicates.map(group=><div className="wh-section" key={group[0].id}>{group.map(w=><div key={w.id}>{work(w)} <small>{w.id.slice(0,8)}</small></div>)}</div>)}<p className="wh-meta">Shows up to 25 matching-title groups.</p></details>
   <details><summary>Related work to review · {report.related_total}</summary>{report.related.map(r=><p key={r.work.id+r.target.id}>{work(r.work)} mentions {work(r.target)} · {r.url}</p>)}<p className="wh-meta">Shows up to 25 pairs with a direct issue or PR link.</p></details>
   <h3>Suggested next priorities</h3>{report.suggested.length?report.suggested.map(w=><p key={w.work_id}>{work({id:w.work_id,title:w.title})} · {w.detail}</p>):<p>No priorities suggested from captured work.</p>}
   <details><summary>Recently completed · {report.completed.total}</summary>{report.completed.items.map((w,i)=><p key={i}>{work({id:w.work_id,title:w.title})}</p>)}</details>
   <details><summary>Running conversations at capture · {report.running.total}</summary>{report.running.items.map((w,i)=><p key={i}>{work({id:w.work_id,title:w.title})}</p>)}</details>
   <h3>Carryover</h3>{report.carryover.length?report.carryover.map(w=><div key={w.id}>{work(w)}</div>):<p>No unfinished items from the saved plan.</p>}{report.return_note&&<p>{report.return_note}</p>}
   <h3>Follow-ups</h3><p>{report.commitments.i_owe||0} owed by you · {report.commitments.they_owe||0} owed to you · {report.commitments.overdue||0} overdue</p>{report.oldest_overdue.map((w,i)=><div key={i}>{work(w)}</div>)}
   <p className="wh-meta">Related source reads · {report.relationship_queue.pending||0} pending · {report.relationship_queue.running||0} running · {report.relationship_queue.error||0} awaiting retry</p>
  </>}
 </Modal>;
}
