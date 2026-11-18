type WorkName={title:string;title_version:number};
type Threads={
 get(args:{threadId:string}):Promise<{title:string|null;deletedAt:number|null}>;
 update(args:{threadId:string;title:string}):Promise<{title:string|null}>;
};

/** A local save and a BB update are separate operations; report partial success. */
export function workRenamer(deps:{
 detail(workId:string):Promise<{work:{conversations:Array<{source:string;external_id:string}>}}>;
 save(workId:string,version:number,title:string):Promise<WorkName>;
 recordThread(workId:string,threadId:string,title:string):Promise<unknown>;
 threads:Threads;changed():void;
}){
 const pending=new Set<string>();
 return async({workId,version,title,threadId}:{workId:string;version:number;title:string;threadId?:string}):Promise<WorkName&{thread_error?:string}>=>{
  if(pending.has(workId))throw new Error('A rename is already in progress for this work. Retry when it finishes.');
  pending.add(workId);
  try{
   if(threadId){
    const detail=await deps.detail(workId);
    if(!detail.work.conversations.some(c=>c.source==='bb'&&c.external_id===threadId))throw new Error('Choose a BB thread currently attached to this work.');
    const thread=await deps.threads.get({threadId});
    if(thread.deletedAt!==null)throw new Error('This BB thread has been deleted. Choose another thread.');
   }
   // CAS happens before the BB write: a stale editor cannot rename a thread.
   const saved=await deps.save(workId,version,title);
   try{
    if(threadId){
     try{
      // Verify membership again after the save; associations can change in another view.
      const latest=await deps.detail(workId);
      if(!latest.work.conversations.some(c=>c.source==='bb'&&c.external_id===threadId))throw new Error('Association removed');
      const current=await deps.threads.get({threadId});
      if(current.deletedAt!==null)throw new Error('Thread deleted');
      if(current.title!==saved.title){
       const renamed=await deps.threads.update({threadId,title:saved.title});
       if(renamed.title!==saved.title)throw new Error('Rename not confirmed');
      }
     }catch{return {...saved,thread_error:'Work name saved, but the BB thread rename could not be confirmed. Retry saving, or uncheck the thread option to keep only the work name.'}}
     try{await deps.recordThread(workId,threadId,saved.title)}
     catch{return {...saved,thread_error:'Both names saved, but the captured thread title could not be refreshed. Retry saving to refresh it.'}}
    }
    return saved;
   }finally{deps.changed()}
  }finally{pending.delete(workId)}
 };
}
