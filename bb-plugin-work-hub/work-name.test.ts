import {test} from 'node:test';
import assert from 'node:assert/strict';
import {workNameError} from './work-name.ts';
import {launchContext} from './launch-context.ts';
import type {Snapshot} from './server';

test('name validation accepts Slack names and Unicode and rejects empty, long and control text',()=>{
 for(const value of ['Slack conversation','Slack · My work','  Launch readiness  ','🚀'.repeat(200)])assert.equal(workNameError(value),'');
 for(const value of ['', '   ', 'x'.repeat(201), 'a\nb', 'a\tb', 'a\0b', 'a\u2028b'])assert.notEqual(workNameError(value),'');
});
test('launch uses renamed work while preserving source conversation title',()=>{
 const snapshot={work:[{id:'w',title:'Slack · My launch',summary:'Progress',next_action:'Review',decisions:[],conversations:[{source:'slack',title:'Slack · DM · Sam · Source excerpt',url:'https://example.slack.com/archives/C123/p123'}]}],obligations:[]} as unknown as Snapshot;
 const launch=launchContext(snapshot,'w');
 assert.equal(launch.title,'Slack · My launch');
 assert.match(launch.prompt,/Slack · My launch/);
 assert.match(launch.prompt,/Slack · DM · Sam · Source excerpt/);
});
