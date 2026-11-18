type PinApi = {
 get(args:{threadId:string}): Promise<{archivedAt:number|null, deletedAt:number|null, pinnedAt:number|null}>;
 pin(args:{threadId:string}): Promise<unknown>;
};

export async function pinFocusedThreads(threadIds:string[], threads:PinApi):Promise<string>{
 let pinned=0, failed=0;
 for(const threadId of new Set(threadIds)){
  try{
   const thread=await threads.get({threadId});
   if(thread.archivedAt!==null || thread.deletedAt!==null || thread.pinnedAt!==null)continue;
   await threads.pin({threadId});
   pinned++;
  }catch{failed++;}
 }
 const message=['Added to focus.'];
 if(pinned)message.push(`Pinned ${pinned} BB thread${pinned===1?'':'s'}.`);
 if(failed)message.push(`Couldn’t pin ${failed} linked thread${failed===1?'':'s'}.`);
 return message.join(' ');
}
