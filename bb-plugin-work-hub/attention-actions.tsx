import {useEffect,useRef,useState} from 'react';
import * as Menu from '@radix-ui/react-dropdown-menu';
import {useRpc} from '@get-bb/plugin-sdk/app';
import {Button} from './components/ui/button';
import {usePortalScopeProps} from './lib/portal-scope';
import type {Attention,Work,WorkDetail,rpcContract} from './server';

export function AttentionActions({item,disabled,onReview,onSeen,onChanged,onFocus,onResume,onStart}:{
 item:Attention;disabled:boolean;onReview:()=>void;onSeen:()=>void;onChanged:()=>void;
 onFocus:(id:string,focused:boolean)=>void;onResume:(work:Work)=>void;onStart:(workId:string,obligationId?:string)=>void;
}){
 const rpc=useRpc<typeof rpcContract>();const scope=usePortalScopeProps();const [open,setOpen]=useState(false);
 const [details,setDetails]=useState<WorkDetail[]|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);const saving=useRef(false);
 useEffect(()=>{if(disabled)setOpen(false)},[disabled]);
 const key=item.work_ids.join(',');
 useEffect(()=>{if(!open)return;let alive=true;setDetails(null);setError('');
  Promise.all(key?key.split(',').map(workId=>rpc.call('workDetail',{workId})):[]).then(d=>{if(alive)setDetails(d)},e=>{if(alive)setError(String(e))});
  return()=>{alive=false};
 },[open,key,item.token,rpc]);
 const obligation=item.kind==='obligation'?details?.flatMap(d=>d.obligations).find(o=>o.id===item.entity_id):undefined;
 const mutable=obligation&&(obligation.state==='open'||obligation.state==='needs_review');
 const mutate=async(kind:'work'|'obligation',id:string,action:'open'|'fulfilled'|'cancelled'|'active'|'waiting'|'done',version?:number)=>{
  if(disabled||saving.current)return;saving.current=true;setBusy(true);setError('');
  try{await rpc.call('mutate',{kind,id,action,...(version===undefined?{}:{version})});onChanged()}
  catch(e){setError(String(e))}finally{saving.current=false;setBusy(false)}
 };
 return <span><Menu.Root open={open} onOpenChange={setOpen}><Menu.Trigger asChild><Button size="sm" variant="outline" disabled={disabled||busy} aria-label={`Actions for ${item.title}`}>Actions ▾</Button></Menu.Trigger><Menu.Portal><Menu.Content {...scope} className="wh-action-menu" sideOffset={6} align="start">
  <Menu.Item onSelect={onReview}>{item.kind==='job'?'Review job':'Review item & evidence'}</Menu.Item>
  {!item.seen&&<Menu.Item onSelect={onSeen}>Mark seen</Menu.Item>}
  {details===null&&!error&&<Menu.Item disabled>Loading allowed actions…</Menu.Item>}
  {error&&<Menu.Item disabled>Could not load actions. Close and retry.</Menu.Item>}
  {mutable&&<><Menu.Item onSelect={async()=>{try{await rpc.call('queueCompletion',{scope:'selected',ids:[obligation.id]});onChanged()}catch(e){setError(String(e))}}}>Check completion</Menu.Item><Menu.Separator/><Menu.Label>Commitment{item.work_ids.length>1?' · shared across work':''}</Menu.Label>
   {obligation.state==='needs_review'&&<Menu.Item onSelect={()=>void mutate('obligation',obligation.id,'open',obligation.version)}>Confirm commitment</Menu.Item>}
   <Menu.Item onSelect={()=>void mutate('obligation',obligation.id,'fulfilled',obligation.version)}>Mark fulfilled</Menu.Item>
   <Menu.Item onSelect={()=>void mutate('obligation',obligation.id,'cancelled',obligation.version)}>Cancel commitment</Menu.Item>
  </>}
  {details?.map(({work,obligations})=><Menu.Group key={work.id}><Menu.Separator/><Menu.Label>{work.title}</Menu.Label>
   <Menu.Item onSelect={()=>onFocus(work.id,!work.focus)}>{work.focus?'Remove focus':'Add to focus'}</Menu.Item>
   {work.conversations.some(c=>c.source==='bb')&&<Menu.Item onSelect={()=>onResume(work)}>Resume conversation…</Menu.Item>}
   {mutable&&obligations.some(o=>o.id===item.entity_id)&&<Menu.Item onSelect={()=>onStart(work.id,item.entity_id)}>Start agent for commitment…</Menu.Item>}
   <Menu.Item onSelect={()=>onStart(work.id)}>Start agent for work…</Menu.Item>
   <Menu.Item onSelect={()=>void mutate('work',work.id,work.status==='waiting'?'active':'waiting')}>{work.status==='waiting'?'Resume work':'Mark work waiting'}</Menu.Item>
   <Menu.Item onSelect={()=>void mutate('work',work.id,work.status==='done'?'active':'done')}>{work.status==='done'?'Reopen work':'Mark work done (commitments stay open)'}</Menu.Item>
  </Menu.Group>)}
 </Menu.Content></Menu.Portal></Menu.Root>{error&&<span className="wh-error" role="alert">{error}</span>}</span>;
}
