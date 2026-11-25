// `bb work-hub doctor`: plugin settings checks. The hub's own checks come from
// hub/doctor.py and are appended by the server. Read-only.

export type Check={level:'ok'|'info'|'warn'|'fail';name:string;detail:string;fix?:string};

export type DoctorConfig={hubPath:string;pythonPath:string;collectThreads:boolean;threadProjects:string;includeHiddenThreads:boolean;autoContext:boolean;remindersEnabled:boolean;nightlyCloseOut:boolean};

export function pluginChecks(config:DoctorConfig,hubFound:boolean,chiefOfStaff:boolean):Check[]{
 const checks:Check[]=[];
 const add=(level:Check['level'],name:string,detail:string,fix?:string)=>checks.push(fix?{level,name,detail,fix}:{level,name,detail});
 if(!config.hubPath.startsWith('/'))add('fail','hubPath','not set to an absolute path','bb plugin config work-hub set hubPath "$WORK_HUB_ROOT/hub"');
 else if(!hubFound)add('fail','hubPath','no hub/cli.py at that path','Point it at the hub folder of your work-hub clone');
 else add('ok','hubPath','points at a hub folder');
 if(!config.pythonPath.startsWith('/'))add('warn','pythonPath',`"${config.pythonPath}" relies on the plugin's PATH`,'bb plugin config work-hub set pythonPath "$WORK_HUB_ROOT/.venv/bin/python"');
 if(!config.collectThreads)add('info','collectThreads','off: BB conversations are not captured','bb plugin config work-hub set collectThreads true');
 else{
  add('ok','collectThreads','on');
  const projects=config.threadProjects.split(',').map(s=>s.trim()).filter(Boolean);
  if(projects.length)add('ok','threadProjects',`${projects.length} project(s) selected`);
  else add('warn','threadProjects','blank: conversations from every BB project are captured','bb plugin config work-hub set threadProjects proj_a,proj_b');
 }
 if(config.includeHiddenThreads)add('warn','includeHiddenThreads','on: internal agent threads are captured too','bb plugin config work-hub set includeHiddenThreads false');
 else add('ok','includeHiddenThreads','off');
 add('info','Optional features',`autoContext ${config.autoContext?'on':'off'}, remindersEnabled ${config.remindersEnabled?'on':'off'}, nightlyCloseOut ${config.nightlyCloseOut?'on':'off'}`);
 add('info','Chief of Staff',chiefOfStaff?'plugin running; dispatch hands jobs to it':'plugin not running (optional)');
 return checks;
}

const MARK={ok:'✓',info:'·',warn:'!',fail:'✗'} as const;

export function doctorText(checks:Check[]):string{
 const width=Math.max(...checks.map(c=>c.name.length));
 const lines=checks.map(c=>`${MARK[c.level]} ${c.name.padEnd(width)}  ${c.detail}${c.fix?`\n  ${' '.repeat(width)}  → ${c.fix}`:''}`);
 const fails=checks.filter(c=>c.level==='fail').length,warns=checks.filter(c=>c.level==='warn').length;
 lines.push('',fails?`${fails} problem(s) to fix, ${warns} warning(s).`:warns?`No problems; ${warns} warning(s) to review.`:'All good.');
 return lines.join('\n');
}
