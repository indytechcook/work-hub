import {useId,useRef,useState} from 'react';
import {useRpc} from '@get-bb/plugin-sdk/app';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription} from '@/components/ui/dialog';
import {Button} from '@/components/ui/button';
import type {rpcContract,Work} from './server';
import {workNameError} from './work-name';

export function WorkNameEditor({work,onClose,onChanged,readOnly=false}:{work:Work;onClose:()=>void;onChanged:()=>void;readOnly?:boolean}){
 const rpc=useRpc<typeof rpcContract>();const id=useId();
 const [title,setTitle]=useState(work.title);const [version,setVersion]=useState(work.title_version);
 const [error,setError]=useState('');const [busy,setBusy]=useState(false);const pending=useRef(false);
 const returnFocus=useRef(document.activeElement as HTMLElement|null);
 const validation=workNameError(title);
 const close=()=>{if(!pending.current)onClose()};
 const save=async()=>{
  if(pending.current||readOnly||validation)return;
  pending.current=true;setBusy(true);setError('');
  try{await rpc.call('renameWork',{workId:work.id,version,title:title.trim()});onChanged();onClose()}
  catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{pending.current=false;setBusy(false)}
 };
 const reload=async()=>{
  if(pending.current)return;pending.current=true;setBusy(true);
  try{const d=await rpc.call('workDetail',{workId:work.id});setTitle(d.work.title);setVersion(d.work.title_version);setError('')}
  catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{pending.current=false;setBusy(false)}
 };
 return <Dialog open onOpenChange={open=>{if(!open)close()}}><DialogContent onCloseAutoFocus={event=>{event.preventDefault();if(returnFocus.current?.isConnected)returnFocus.current.focus()}} className="wh-modal sm:max-w-lg"><DialogHeader><DialogTitle>Rename work</DialogTitle><DialogDescription>Choose a name that stays through source updates and analysis. Related conversations keep their own titles.</DialogDescription></DialogHeader>
  <form className="wh-form" aria-busy={busy} onSubmit={event=>{event.preventDefault();void save()}}>
   <label htmlFor={id}>Work name</label><input id={id} autoFocus value={title} disabled={busy||readOnly} maxLength={1000} aria-invalid={!!validation} aria-describedby={`${id}-hint${validation?` ${id}-validation`:''}`} onChange={event=>setTitle(event.target.value)}/>
   <p id={`${id}-hint`} className="wh-meta">1–200 characters. Surrounding spaces are trimmed.</p>
   {validation&&<p id={`${id}-validation`} role="alert">{validation}</p>}
   {error&&<div><p role="alert">{error}</p><Button type="button" variant="outline" disabled={busy} onClick={()=>void reload()}>Reload saved name (discard edits)</Button></div>}
   <div className="wh-actions"><Button type="button" variant="outline" disabled={busy} onClick={close}>Cancel</Button><Button type="submit" disabled={busy||readOnly||!!validation}>{busy?'Saving…':'Save name'}</Button></div>
  </form>
 </DialogContent></Dialog>;
}
