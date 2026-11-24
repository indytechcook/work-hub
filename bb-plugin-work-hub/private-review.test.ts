import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('../hub/private-review.js',import.meta.url),'utf8')
 .replace('export const meta','const meta');
const execute=new Function('args','agent',`return (async()=>{${source}})()`);
const args={url:'https://github.com/example/repo/pull/1',base:'a'.repeat(40),head:'b'.repeat(40)};
const complete={...args,state:'complete',synthesis:'/reports/synthesis.md',correctness:'/reports/correctness.md',passes:['Correctness'],summary:'Checked the revision.'};
test('private workflow verifies pass evidence and rejects stale or incomplete reviews',async()=>{
 const run=(verification:typeof complete)=>execute(args,async (_:string, options:{phase:string})=>options.phase==='Review'?'Report paths':verification);
 assert.deepEqual(await run(complete),complete);
 for(const changes of [{head:'c'.repeat(40)},{state:'incomplete'},{correctness:''},{passes:[]}]){
  await assert.rejects(run({...complete,...changes}),/requires attention/);
 }
 await assert.rejects(execute({...args,head:'branch'},()=>assert.fail()),/exact base and head/);
});
