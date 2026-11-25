import {test} from 'node:test';
import assert from 'node:assert/strict';
import {doctorText,pluginChecks,type DoctorConfig} from './doctor.ts';

const base:DoctorConfig={hubPath:'/opt/work-hub/hub',pythonPath:'/opt/work-hub/.venv/bin/python',collectThreads:true,threadProjects:'proj_a',includeHiddenThreads:false,autoContext:false,remindersEnabled:false,nightlyCloseOut:false};
const level=(c:ReturnType<typeof pluginChecks>,name:string)=>c.find(x=>x.name===name)?.level;

test('a recommended setup has no warnings',()=>{
 const checks=pluginChecks(base,true,false);
 assert.deepEqual(checks.filter(c=>c.level==='warn'||c.level==='fail'),[]);
 assert.match(doctorText(checks),/All good\./);
});

test('missing paths and broad capture are reported',()=>{
 const checks=pluginChecks({...base,hubPath:'',pythonPath:'python3',threadProjects:' ',includeHiddenThreads:true},false,true);
 assert.equal(level(checks,'hubPath'),'fail');
 assert.equal(level(checks,'pythonPath'),'warn');
 assert.equal(level(checks,'threadProjects'),'warn');
 assert.equal(level(checks,'includeHiddenThreads'),'warn');
 assert.match(doctorText(checks),/1 problem\(s\) to fix, 3 warning\(s\)/);
});

test('a relative hub path or a folder without cli.py fails',()=>{
 assert.equal(level(pluginChecks({...base,hubPath:'hub'},false,false),'hubPath'),'fail');
 assert.match(pluginChecks(base,false,false).find(c=>c.name==='hubPath')!.detail,/no hub\/cli.py/);
});
