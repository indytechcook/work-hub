import { UrlLink } from '@get-bb/plugin-sdk/app';
import { referenceLinks } from './reference-links';

export function LinkedText({text,links,title=false}:{text:string;links:ReturnType<typeof referenceLinks>;title?:boolean}){
 const href=title?links.titleUrl(text):undefined;
 const style='underline decoration-current/40 underline-offset-2 hover:decoration-current';
 return href?<UrlLink className={style} href={href}>{text}</UrlLink>:<>{links.parts(text).map((p,i)=>p.href?<UrlLink key={i} className={style} href={p.href}>{p.text}</UrlLink>:p.text)}</>;
}
