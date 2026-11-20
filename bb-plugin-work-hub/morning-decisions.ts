import type {MorningRun} from './morning-run';

export const branchReviewInstructions = `For branch and PR reviews, including re-reviews, invoke /review-branch unless the user explicitly requests a lite review. Include this instruction, the verified base and head, and the requested scope in every review child's launch prompt. If slash invocation is unavailable, load the review-branch SKILL.md and execute its referenced workflow against this checkout. If the skill cannot be found, report the missing skill. A small diff, private review or previously approved revision still gets the standard workflow. Run its required correctness and applicable gap passes. Before reporting completion, check the returned synthesis and correctness report for the requested revision, passes actually run and any fallback the skill permits. If a required pass was skipped or reviewed another diff, correct it or report the review as incomplete. A lite review may use a focused inspection and should be labeled lite.`;

export const briefingInstructions = `Lead with one concrete next move: what to do, why now, and the original link. Use my stated goals and saved order first. Check the calendar, near deadlines, and any blocking decision for a reason to change that order. Prefer work that unblocks a person or a scheduled event today over old backlog. If I name a priority in this conversation, act on that correction. Offer at most two other moves, only when they change today's choice. Keep each to one sentence. Read phase details as needed; show the backlog and category breakdown only when requested or when the decision depends on them. Mention a coverage gap if it affects the recommendation. If evidence is insufficient, ask one targeted choice instead of inventing work.
When I ask about reviews, offer ONE specific PR with the reason it is ready. Prefer a new revision after changes requested. Give me a card with Start private review #N, Show another, or Skip. Before offering or starting, check the current head, review request, existing reviews and linked BB threads for a review already underway. If I choose Start, create one new visible child thread and a fresh persistent workspace for this review. Use bb thread spawn with the matching project, --parent-self --visibility visible --new-environment worktree and the verified PR head ref as the base branch. Leave lifecycle ownership independent. In the child prompt, require the reviewer to check out the actual PR head branch in its own clean workspace, verify HEAD against the requested SHA, set the environment merge base to the PR base remote ref, and verify bb pull-request show reports the exact PR URL before reviewing. A generated bb/... branch at the same SHA can leave BB's PR panel empty. If the branch is occupied by an earlier review, diverged, or ambiguous across forks, use a fresh persistent independent clone registered as the new thread's environment; preserve all existing checkouts. ${branchReviewInstructions} Return the visible thread link. Keep the thread, worktree and local review branch available after completion, posting, or morning close-out. Archive or remove them only on my explicit request. If evidence changed, offer the next eligible PR. Keep publication on GitHub a separate decision.`;

export function morningDecisions(run:MorningRun, savedOrder:ReadonlyArray<{id:string;title:string}>){
 const sections=run.phases.find(p=>p.key==='brief')?.sections??[];
 const ranked=sections.find(s=>s.label==='Suggested actions')?.items??[];
 const ordered=savedOrder.map(w=>ranked.find(r=>r.work_id===w.id)??{
  work_id:w.id,title:w.title,url:'',detail:'In your saved plan',
 });
 const priorities=[...ordered,...ranked.filter(r=>!ordered.some(w=>w.work_id===r.work_id))];
 const prs=run.phases.find(p=>p.key==='prs')?.sections??[];
 const reviews=prs.find(s=>s.label==='Review candidates');
 const standupState=run.phases.find(p=>p.key==='standup')?.sections[0]?.state;
 return {
  standup:standupState==='checked'?'pending':standupState,
  startWith:priorities[0]??null,
  alternatives:priorities.slice(1,3),
  nextCalendarItem:sections.find(s=>s.label==='Calendar and week ahead')?.items[0]??null,
  reviewCandidate:reviews?.items[0]??null,
  reviewCheck:reviews?.state??'unverified',
  coverageGaps:run.phases.flatMap(p=>p.sections.filter(s=>s.state==='failed'||s.state==='partial')
   .map(s=>({phase:p.key,label:s.label,detail:s.detail}))),
 };
}
