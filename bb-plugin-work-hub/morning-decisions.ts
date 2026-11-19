import type {MorningRun} from './morning-run';

export const briefingInstructions = `Lead with one concrete next move: what to do, why now, and the original link. Use my stated goals and saved order first. Check the calendar, near deadlines, and any blocking decision for a reason to change that order. Prefer work that unblocks a person or a scheduled event today over old backlog. If I name a priority in this conversation, act on that correction. Offer at most two other moves, only when they change today's choice. Keep each to one sentence. Read phase details as needed; show the backlog, standup draft, and category breakdown only when requested or when the decision depends on them. Mention a coverage gap if it affects the recommendation. If evidence is insufficient, ask one targeted choice instead of inventing work.
When I ask about reviews, offer ONE specific PR with the reason it is ready. Prefer a new revision after changes requested. Give me a card with Start private review #N, Show another, or Skip. Before offering or starting, check the current head, review request, existing reviews and linked BB threads for a review already underway. If I choose Start, launch one linked child BB thread in the matching repository's worktree to prepare a private review of the selected revision. Use the repository's review instructions and return the thread link. If evidence changed, offer the next eligible PR. Keep publication on GitHub a separate decision.`;

export function morningDecisions(run:MorningRun, savedOrder:ReadonlyArray<{id:string;title:string}>){
 const sections=run.phases.find(p=>p.key==='brief')?.sections??[];
 const ranked=sections.find(s=>s.label==='Suggested actions')?.items??[];
 const ordered=savedOrder.map(w=>ranked.find(r=>r.work_id===w.id)??{
  work_id:w.id,title:w.title,url:'',detail:'In your saved plan',
 });
 const priorities=[...ordered,...ranked.filter(r=>!ordered.some(w=>w.work_id===r.work_id))];
 const prs=run.phases.find(p=>p.key==='prs')?.sections??[];
 const reviews=prs.find(s=>s.label==='Review candidates');
 return {
  startWith:priorities[0]??null,
  alternatives:priorities.slice(1,3),
  nextCalendarItem:sections.find(s=>s.label==='Calendar and week ahead')?.items[0]??null,
  reviewCandidate:reviews?.items[0]??null,
  reviewCheck:reviews?.state??'unverified',
  coverageGaps:run.phases.flatMap(p=>p.sections.filter(s=>s.state==='failed'||s.state==='partial')
   .map(s=>({phase:p.key,label:s.label,detail:s.detail}))),
 };
}
