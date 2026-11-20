import {useState} from 'react';
import type {PluginPendingInteractionProps} from '@get-bb/plugin-sdk/app';
import {Button} from '@/components/ui/button';
import {standupPayload} from './standup';

export function StandupForm({interaction,submit,cancel}:PluginPendingInteractionProps){
 const payload=standupPayload.parse(interaction.payload);
 const [answers,setAnswers]=useState(payload.draft.answers);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 async function finish(send:boolean){
  setBusy(true);
  try{if(send)await submit(answers);else await cancel()}
  catch{setError('Could not finish this request. Please try again.');setBusy(false)}
 }
 return <fieldset disabled={busy} className="flex flex-col gap-3 p-3">
  <legend>Daily standup · {payload.form.date}</legend>
  {payload.form.questions.map((q,i)=>{
   const change=(value:string)=>setAnswers(current=>current.map((old,j)=>i===j?value:old));
   return <label key={q.id} className="flex flex-col gap-1 whitespace-pre-line">{q.text}
    {q.type==='boolean'?<select value={answers[i]} onChange={e=>change(e.target.value)}>
     <option value="">Choose an answer</option><option value="yes">Yes</option><option value="no">No</option>
    </select>:<textarea className="rounded border p-2" rows={3} maxLength={4000} value={answers[i]} onChange={e=>change(e.target.value)}/>}
   </label>;
  })}
  <Button disabled={answers.some(a=>!a.trim())} onClick={()=>void finish(true)}>Submit to DailyBot</Button>
  <Button variant="ghost" onClick={()=>void finish(false)}>Later</Button>
  {error&&<p role="alert">{error}</p>}
 </fieldset>;
}
