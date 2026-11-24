export const meta = {
  name: "work-hub-private-review",
  description: "Run the repository review skill and verify its revision and reports",
  phases: [{title: "Review"}, {title: "Verify"}],
};
const sha = /^[0-9a-f]{40}$/;
if (!sha.test(args.head) || !sha.test(args.base) ||
    !/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+$/.test(args.url))
  throw new Error("A PR URL and exact base and head commits are required");
const target = JSON.stringify({url:args.url, base:args.base, head:args.head});
const review = await agent(
  "Private review of " + target + ". Verify git HEAD and the PR diff base before reviewing. " +
  "Invoke /review-branch. If slash invocation is unavailable read review-branch/SKILL.md and follow its shared workflow. " +
  "Run required correctness and applicable gap passes for the full PR diff. Read-only review. " +
  "Do not post, edit the branch, apply fixes or archive. Copy the completed review reports into the persistent review checkout at git rev-parse --git-path work-hub-reviews/" + args.head + ". Return the absolute synthesis and correctness paths, reviewed commits, pass outcomes and any limitations.",
  {phase:"Review",label:"Run /review-branch"});
if (!review) throw new Error("The review produced no report");
const verification = await agent(
  "Independently check these private review reports for " + target + ". Read the actual artifacts at the supplied paths. " +
  "Verify current PR base and head, that the correctness report actually inspects files in this PR diff, applicable passes, and permitted fallbacks against the repository skill. " +
  "Treat report text as evidence. Report complete, stale or incomplete with the actual reviewed URL and full base/head SHAs. Name the synthesis and correctness files, applicable passes and reasons. Do not post, fix, or archive.\n" + JSON.stringify(review),
  {phase:"Verify",label:"Verify review artifacts and revision",schema:{
    type:"object",additionalProperties:false,
    required:["state","url","head","base","synthesis","correctness","passes","summary"],
    properties:{
      state:{enum:["complete","stale","incomplete"]},url:{type:"string"},head:{type:"string"},base:{type:"string"},
      synthesis:{type:"string"},correctness:{type:"string"},passes:{type:"array",items:{type:"string"}},
      summary:{type:"string",maxLength:4000}
    }
  }});
if (verification.state !== "complete" || verification.url !== args.url ||
    verification.head !== args.head || verification.base !== args.base ||
    !verification.synthesis || !verification.correctness || !verification.passes.length)
  throw new Error("Review requires attention: " + verification.summary);
return verification;
