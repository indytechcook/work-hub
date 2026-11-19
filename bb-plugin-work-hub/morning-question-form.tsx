import {useState} from 'react';
import type {PluginPendingInteractionProps} from '@get-bb/plugin-sdk/app';
import {Button} from '@/components/ui/button';
import {morningQuestion} from './morning-question';

export function MorningQuestionForm({interaction,submit,cancel}:PluginPendingInteractionProps){
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const parsed=morningQuestion.safeParse(interaction.payload);
 async function answer(choice?:string){
  setBusy(true);
  setError('');
  try{if(choice===undefined)await cancel();else await submit(choice)}
  catch(e){setError(String(e));setBusy(false)}
 }
 return <fieldset disabled={busy} className="flex flex-col items-start gap-2 p-3">
  <legend>{parsed.success?parsed.data.question:'This planning question could not be displayed.'}</legend>
  {parsed.success&&parsed.data.choices.map(choice=><Button key={choice} variant="outline" onClick={()=>void answer(choice)}>{choice}</Button>)}
  <Button variant="ghost" onClick={()=>void answer()}>Dismiss</Button>
  {error&&<p role="alert">{error}</p>}
 </fieldset>;
}
