import {useId,useRef,useState} from 'react';
import {useRpc} from '@get-bb/plugin-sdk/app';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription} from '@/components/ui/dialog';
import {Button} from '@/components/ui/button';
import {Checkbox} from '@/components/ui/checkbox';
import type {rpcContract,Work} from './server';
import {workNameError} from './work-name';

export function WorkNameEditor({work,onClose,onChanged,readOnly=false,currentThreadId}:{work:Work;onClose:()=>void;onChanged:()=>void;readOnly?:boolean;currentThreadId?:string}){
 const rpc=useRpc<typeof rpcContract>();const id=useId();
 const [title,setTitle]=useState(work.title);const [version,setVersion]=useState(work.title_version);
 const threads=work.conversations.filter(c=>c.source==='bb');
 const [renameThread,setRenameThread]=useState(false);
 const [threadId,setThreadId]=useState(()=>threads.find(c=>c.external_id===currentThreadId)?.external_id??(threads.length===1?threads[0].external_id:''));
 const threadSelected=threads.some(c=>c.external_id===threadId);
 const [error,setError]=useState('');const [busy,setBusy]=useState(false);const pending=useRef(false);
 const returnFocus=useRef(document.activeElement as HTMLElement|null);
 const validation=workNameError(title);
 const close=()=>{if(!pending.current)onClose()};
 const save=async()=>{
  if(pending.current||readOnly||validation||(renameThread&&!threadSelected))return;
  pending.current=true;setBusy(true);setError('');
  try{const saved=await rpc.call('renameWork',{workId:work.id,version,title:title.trim(),...(renameThread?{threadId}:{})});setTitle(saved.title);setVersion(saved.title_version);onChanged();if(saved.thread_error)setError(saved.thread_error);else onClose()}
  catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{pending.current=false;setBusy(false)}
 };
 const reload=async()=>{
  if(pending.current)return;pending.current=true;setBusy(true);
  try{const d=await rpc.call('workDetail',{workId:work.id});setTitle(d.work.title);setVersion(d.work.title_version);setError('')}
  catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{pending.current=false;setBusy(false)}
 };
 return <Dialog open onOpenChange={open=>{if(!open)close()}}><DialogContent onCloseAutoFocus={event=>{event.preventDefault();if(returnFocus.current?.isConnected)returnFocus.current.focus()}} className="wh-modal max-h-[85vh] overflow-auto sm:max-w-lg"><DialogHeader><DialogTitle>Rename work</DialogTitle><DialogDescription>Choose a name that stays through source updates and analysis. You can also give an attached BB thread the same name.</DialogDescription></DialogHeader>
  <form className="wh-form" aria-busy={busy} onSubmit={event=>{event.preventDefault();void save()}}>
   <label htmlFor={id}>Work name</label><input id={id} autoFocus value={title} disabled={busy||readOnly} maxLength={1000} aria-invalid={!!validation} aria-describedby={`${id}-hint${validation?` ${id}-validation`:''}`} onChange={event=>setTitle(event.target.value)}/>
   <p id={`${id}-hint`} className="wh-meta">1–200 characters. Surrounding spaces are trimmed.</p>
   {threads.length>0?<div><div className="flex items-center gap-2"><Checkbox id={`${id}-rename-thread`} checked={renameThread} disabled={busy||readOnly} onCheckedChange={checked=>setRenameThread(checked===true)}/><label htmlFor={`${id}-rename-thread`}>Also rename a BB thread</label></div>{renameThread&&<label className="mt-3">BB thread to rename<select aria-label="BB thread to rename" value={threadSelected?threadId:''} disabled={busy||readOnly} onChange={event=>setThreadId(event.target.value)}><option value="">Choose an attached thread…</option>{threads.map(c=><option key={c.id} value={c.external_id}>{c.title}{c.external_id===currentThreadId?' · Current thread':''} · {c.model||'BB'}</option>)}</select><span className="wh-meta">This changes the selected thread’s title in BB. Other threads keep their names.</span></label>}</div>:<p className="wh-meta">No BB thread is attached to this work.</p>}
   {validation&&<p id={`${id}-validation`} role="alert">{validation}</p>}
   {error&&<div><p role="alert">{error}</p><Button type="button" variant="outline" disabled={busy} onClick={()=>void reload()}>Reload saved name (discard edits)</Button></div>}
   <div className="wh-actions"><Button type="button" variant="outline" disabled={busy} onClick={close}>Cancel</Button><Button type="submit" disabled={busy||readOnly||!!validation||(renameThread&&!threadSelected)}>{busy?'Saving…':renameThread?'Save both names':'Save name'}</Button></div>
  </form>
 </DialogContent></Dialog>;
}
