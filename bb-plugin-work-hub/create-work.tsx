import {useState} from 'react';
import {useRpc} from '@get-bb/plugin-sdk/app';
import {Button} from '@/components/ui/button';
import type {rpcContract} from './server';
import {Modal} from './work-ui';
import {workNameError} from './work-name';

export function CreateWork({onClose,onCreated}:{onClose:()=>void;onCreated:(id:string)=>void}){
 const rpc = useRpc<typeof rpcContract>();
 const [requestId] = useState(()=>crypto.randomUUID());
 const [title,setTitle] = useState('');
 const [purpose,setPurpose] = useState('');
 const [busy,setBusy] = useState(false);
 const [error,setError] = useState('');
 async function submit(event:React.FormEvent){
  event.preventDefault();
  if(busy)return;
  setBusy(true);
  setError('');
  try{
   const item = await rpc.call('createWork',{title,purpose,requestId});
   onCreated(item.id);
  }catch(e){setError(String(e))}
  finally{setBusy(false)}
 }
 return <Modal title="Create work item" description="Save personal work in Work Hub. You can attach sources later." onClose={()=>{if(!busy)onClose()}}>
  <form className="wh-form" onSubmit={submit}>
   <label>Work name<input required value={title} maxLength={200} disabled={busy} onChange={e=>setTitle(e.target.value)}/></label>
   <label>Purpose (optional)<textarea value={purpose} maxLength={3000} disabled={busy} onChange={e=>setPurpose(e.target.value)}/></label>
   {error&&<p role="alert">{error}</p>}
   <Button type="submit" disabled={busy||!!workNameError(title)}>{busy?'Saving…':'Create work item'}</Button>
  </form>
 </Modal>;
}
