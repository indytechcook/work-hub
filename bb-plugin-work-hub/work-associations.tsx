import {useCallback,useEffect,useRef,useState} from 'react';
import {useRpc,useRealtime} from '@get-bb/plugin-sdk/app';
import type {rpcContract,WorkOptions} from './server';
import {Button} from '@/components/ui/button';

export function WorkAssociations({conversationId,title,count,onChanged,readOnly=false}:{conversationId:string;title:string;count:number;onChanged:()=>void;readOnly?:boolean}){
 const rpc=useRpc<typeof rpcContract>();
 const [expanded,setExpanded]=useState(false);const [query,setQuery]=useState('');
 const [data,setData]=useState<WorkOptions|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const request=useRef(0);const alive=useRef(true);const saving=useRef(false);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;request.current++;}},[]);
 const load=useCallback(async()=>{
  const n=++request.current;
  try{const result=await rpc.call('workOptions',{conversationId,query});if(alive.current&&n===request.current){setData(result);setError('');}}
  catch(e){if(alive.current&&n===request.current)setError(e instanceof Error?e.message:String(e));}
 },[rpc,conversationId,query]);
 useEffect(()=>{if(!expanded)return;const timer=setTimeout(()=>void load(),200);return()=>{clearTimeout(timer);request.current++;};},[expanded,load]);
 useRealtime('changed',useCallback(()=>{if(expanded)void load();},[expanded,load]));
 const update=async(workId:string,action:'add'|'remove')=>{
  if(saving.current||readOnly)return;saving.current=true;setBusy(true);
  try{await rpc.call('associate',{conversationId,workId,action});if(alive.current){await load();onChanged();}}
  catch(e){if(alive.current)setError(e instanceof Error?e.message:String(e));}
  finally{saving.current=false;if(alive.current)setBusy(false);}
 };
 return <details className="mt-2 text-xs" onToggle={e=>setExpanded(e.currentTarget.open)}>
  <summary aria-label={`Manage work associations for ${title}`} className="cursor-pointer font-medium">Associated work ({count}) · Manage</summary>
  {expanded&&<div className="mt-2 space-y-3 rounded border border-border bg-background p-3">
   <p className="text-muted-foreground">Add this item to more than one work item. Its decisions and follow-ups are shared, not copied.</p>
   {error&&<p role="alert" className="text-destructive">{error}</p>}
   {!data&&!error&&<p>Loading associations…</p>}
   {data&&<><ul className="space-y-2">{data.linked.map(w=><li key={w.id} className="flex items-start justify-between gap-2"><span>{w.title}<span className="block text-muted-foreground">{w.status}</span></span><Button size="sm" variant="ghost" disabled={readOnly||busy||data.linked.length===1} aria-label={`Remove association with ${w.title}`} onClick={()=>void update(w.id,'remove')}>Remove</Button></li>)}</ul><p className="text-muted-foreground">Keep at least one association. Removing a link does not delete this item or its commitments.</p></>}
   <input aria-label={`Find work to associate with ${title}`} maxLength={200} value={query} onChange={e=>setQuery(e.target.value)} placeholder="Find another work item…" className="w-full rounded border border-input bg-background p-2"/>
   {data&&<><ul className="max-h-56 space-y-2 overflow-auto">{data.options.map(w=><li key={w.id} className="flex items-start justify-between gap-2"><span>{w.title}<span className="block text-muted-foreground">{w.status}</span></span><Button size="sm" variant="outline" disabled={readOnly||busy} aria-label={`Associate with ${w.title}`} onClick={()=>void update(w.id,'add')}>Add</Button></li>)}</ul>{data.options.length===0&&<p>No other matching work items.</p>}{data.has_more&&<p className="text-muted-foreground">Showing 50 matches. Refine your search to find another item.</p>}</>}
  </div>}
 </details>;
}
