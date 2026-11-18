import assert from 'node:assert/strict';
import test from 'node:test';
import {pinFocusedThreads} from './focus-pins.ts';

test('only eligible threads are pinned and one failure does not block the others',async()=>{
 const attempted:string[]=[];
 const notice=await pinFocusedThreads(['gone','broken','live','archived','deleted','pinned','live'],{
  async get({threadId}){
   if(threadId==='gone')throw Error();
   return {archivedAt:threadId==='archived'?1:null,deletedAt:threadId==='deleted'?1:null,pinnedAt:threadId==='pinned'?1:null};
  },
  async pin({threadId}){attempted.push(threadId);if(threadId==='broken')throw Error();}
 });
 assert.deepEqual(attempted,['broken','live']);
 assert.equal(notice,'Added to focus. Pinned 1 BB thread. Couldn’t pin 2 linked threads.');
});
