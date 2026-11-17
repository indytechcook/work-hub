import {useCallback,useEffect,useState} from 'react';
import {useRealtime,useRpc} from '@get-bb/plugin-sdk/app';
import type {CompletionPage,rpcContract} from './server';
import {Button} from './components/ui/button';
import {Modal,when} from './work-ui';
import {referenceLinks} from './reference-links';
import {LinkedText} from './linked-text';

export function CompletionReview({onClose,onChanged}:{onClose:()=>void;onChanged:()=>void}){
 const rpc=useRpc<typeof rpcContract>();const [page,setPage]=useState<CompletionPage|null>(null);const [offset,setOffset]=useState(0);const [verdict,setVerdict]=useState('all');const [error,setError]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);
 const refresh=useCallback(async()=>{try{setPage(await rpc.call('completionPage',{offset,verdict}));setError('')}catch(e){setError(String(e))}},[rpc,offset,verdict]);
 useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),10000);return()=>clearInterval(timer)},[refresh]);useRealtime('changed',refresh);
 const queue=async(scope:'unseen'|'open')=>{setBusy(true);try{const r=await rpc.call('queueCompletion',{scope});setNotice(`${r.queued} commitments queued. Checks use the configured analyzer and shared daily allowance.`);await refresh()}catch(e){setError(String(e))}finally{setBusy(false)}};
 const resolve=async(ids:string[])=>{setBusy(true);let completed=0,skipped=0;for(const obligationId of ids){try{await rpc.call('resolveCompletion',{obligationId});completed++}catch{skipped++}}setNotice(`${completed} verified completions saved.${skipped?` ${skipped} changed or could not be saved; check them again.`:''}`);setBusy(false);await refresh();onChanged()};
 const change=async(row:CompletionPage['items'][number],action:'open'|'fulfilled'|'cancelled')=>{setBusy(true);try{await rpc.call('mutate',{kind:'obligation',id:row.obligation_id,action,version:row.current_version,value:'Manually '+action+' from completion review'});await refresh();onChanged()}catch(e){setError(String(e))}finally{setBusy(false)}};
 const ready=page?.items.filter(r=>r.state==='done'&&r.verdict==='confirmed_complete'&&r.applied_version===null&&r.obligation_version===r.current_version&&['open','needs_review'].includes(r.commitment_state))??[];
 return <Modal title="Check completion" description="Verify the specific promise against related sources. Unconfirmed means insufficient evidence; marking seen is separate." onClose={onClose}>
  <div className="wh-actions"><Button disabled={busy} onClick={()=>void queue('unseen')}>Check all unseen</Button><Button disabled={busy} variant="outline" onClick={()=>void queue('open')}>Check all open commitments</Button></div>
  <p className="wh-meta">Background checks share your analysis allowance. Pending checks resume after sleep or restart. Automatic resolution is off unless enabled in policy.</p>
  {notice&&<p role="status">{notice}</p>}{error&&<p className="wh-error" role="alert">{error}</p>}
  {page&&<p className="wh-callout">{page.budget.remaining>0?`${page.budget.remaining} of ${page.budget.limit} shared analysis calls remain today.`:'Daily analysis allowance reached. Queued checks resume after the UTC daily reset, or an explicit policy change.'}</p>}
  {page&&<p>{page.counts.queued||0} queued · {page.counts.running||0} checking · {page.counts.done||0} assessed · {page.counts.error||0} awaiting retry</p>}
  <label>Assessment <select value={verdict} onChange={e=>{setVerdict(e.target.value);setOffset(0)}}>{['all','pending','confirmed_complete','partly_complete','still_open','upcoming','superseded','unconfirmed'].map(v=><option key={v} value={v}>{v.replaceAll('_',' ')}</option>)}</select></label>
  {ready.length>0&&<Button disabled={busy||!!error} onClick={()=>void resolve(ready.map(r=>r.obligation_id))}>Resolve {ready.length} confirmed on this page</Button>}
  {page?.items.map(row=><article className="wh-source" key={row.obligation_id}><h3><LinkedText text={row.title} links={referenceLinks([{title:row.title,url:row.url}])}/></h3><p><strong>{row.state==='done'?row.verdict?.replaceAll('_',' '):row.state}</strong> · commitment {row.commitment_state}</p>{row.reason&&<p>{row.reason}</p>}{row.error&&<p role="status">{row.error}</p>}<p className="wh-meta">Last assessed {when(row.checked_at)}</p>
   {row.evidence.map((e,i)=><blockquote key={i}><p>{e.quote}</p><LinkedText text="View evidence" links={referenceLinks([{title:'View evidence',url:e.url}])}/></blockquote>)}
   <div className="wh-actions"><Button size="sm" variant="outline" disabled={busy} onClick={async()=>{setBusy(true);try{await rpc.call('queueCompletion',{scope:'selected',ids:[row.obligation_id]});await refresh()}catch(e){setError(String(e))}finally{setBusy(false)}}}>Check again</Button>
   {ready.some(r=>r.obligation_id===row.obligation_id)&&<Button size="sm" disabled={busy} onClick={()=>void resolve([row.obligation_id])}>Resolve verified</Button>}
   <Button size="sm" variant="ghost" disabled={busy||!!error} onClick={()=>void change(row,'open')}>Mark open</Button><Button size="sm" variant="ghost" disabled={busy||!!error} onClick={()=>void change(row,'fulfilled')}>Mark fulfilled manually</Button><Button size="sm" variant="ghost" disabled={busy||!!error} onClick={()=>void change(row,'cancelled')}>Cancel commitment</Button></div>
  </article>)}
  {page?.total===0&&<p>No assessments in this view. Start a completion check or change the filter.</p>}
  <div className="wh-actions"><Button variant="ghost" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>Previous</Button><span>{page?.total??0} assessments</span><Button variant="ghost" disabled={!page?.has_more} onClick={()=>setOffset(offset+50)}>Next</Button></div>
 </Modal>;
}
