type Source = {title:string;url:string};
export type LinkPart = {text:string;href?:string};

function sourceReference(raw:string){
 try{
  const u=new URL(raw);
  if(u.protocol!=='https:'||u.username||u.password)return null;
  const pr=u.hostname==='github.com'&&u.pathname.match(/^\/([^/]+\/[^/]+)\/pull\/(\d+)(?:\/|$)/);
  if(pr)return {kind:'pr' as const,key:pr[2],repo:pr[1],url:raw};
  const issue=u.hostname==='linear.app'&&u.pathname.match(/^\/([^/]+)\/issue\/([A-Z][A-Z0-9]*-\d+)(?:\/|$)/i);
  if(issue)return {kind:'issue' as const,key:issue[2].toUpperCase(),repo:issue[1],url:raw};
 }catch{/* Non-web conversation identifiers are not external links. */}
 return null;
}

export function referenceLinks(sources:Source[],context:Source[]=[]){
 const refs=sources.map(s=>sourceReference(s.url)).filter(r=>r!==null);
 const local=context.map(s=>sourceReference(s.url)).filter(r=>r!==null);
 const unique=(values:string[])=>{const v=[...new Set(values)];return v.length===1?v[0]:undefined};
 const resolve=(token:string):string|undefined=>{
  if(/^https?:\/\//i.test(token))return token;
  if(token.includes('#')){
   const full=token.match(/^([\w.-]+\/[\w.-]+)#(\d+)$/);
   if(full)return `https://github.com/${full[1]}/pull/${full[2]}`;
   const number=token.match(/#(\d+)$/)?.[1];
   const repo=unique(local.filter(r=>r.kind==='pr').map(r=>r.repo));
   const matches=refs.filter(r=>r.kind==='pr'&&r.key===number&&(!repo||r.repo===repo));
   const known=unique(matches.map(r=>`https://github.com/${r.repo}/pull/${r.key}`));
   return known??(repo&&/^PR\s/i.test(token)?`https://github.com/${repo}/pull/${number}`:undefined);
  }
  const key=token.toUpperCase();
  const known=unique(refs.filter(r=>r.kind==='issue'&&r.key===key).map(r=>r.url));
  if(known)return known;
  const team=key.split('-')[0];
  const workspace=unique(refs.filter(r=>r.kind==='issue'&&r.key.startsWith(team+'-')).map(r=>r.repo));
  return workspace?`https://linear.app/${workspace}/issue/${key}`:undefined;
 };
 return {
  titleUrl(title:string){return unique(context.filter(s=>s.title===title&&sourceReference(s.url)).map(s=>s.url))},
  parts(text:string):LinkPart[]{
   const parts:LinkPart[]=[];let offset=0;
   const pattern=/https?:\/\/[^\s<>"`]+|\b[\w.-]+\/[\w.-]+#\d+\b|\bPR\s+#\d+\b|(?<![\w/])#\d+\b|\b[A-Z][A-Z0-9]*-\d+\b/g;
   for(const m of text.matchAll(pattern)){
    const start=m.index!;
    let token=m[0];
    if(/^https?:/.test(token)){
     token=token.replace(/[.,;:!?]+$/,'');
     while(token.endsWith(')')&&(token.match(/\)/g)||[]).length>(token.match(/\(/g)||[]).length)token=token.slice(0,-1);
    }
    const href=resolve(token);
    if(!href)continue;
    if(start>offset)parts.push({text:text.slice(offset,start)});
    parts.push({text:token,href});offset=start+token.length;
   }
   if(offset<text.length)parts.push({text:text.slice(offset)});
   return parts;
  }
 };
}
