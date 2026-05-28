# Reddit Fresh Opening Templates

_Use these structures as starting points. Rotate, adapt, never copy verbatim._

## Pattern 1: Failure-first observation
**Structure:** State a common failure → explain why it happens → give the structural fix
**Example:**
> The thing that breaks first in unattended AI coding runs is not the model — it's the finish line.
> Most agents stop when they run out of tokens or hit a loop. The fix is not a better prompt. It's a spec that defines done before the agent starts, and a review surface that makes the result auditable without re-running everything.

**When to use:** Threads about overnight runs, autonomous coding, unattended agents.

---

## Pattern 2: Reframe the premise
**Structure:** Reject the common assumption → offer the real question
**Example:**
> The question is not which model is smartest. It's which workflow actually ends in something you can review instead of a transcript you have to reconstruct.
> I've been running Claude Code, Codex, and OpenCode on real tasks overnight. The model matters less than whether the run finishes with a diff, checks, and a short note about what still needs a human decision.

**When to use:** Threads comparing AI coding tools, "which should I use" posts.

---

## Pattern 3: Concrete failure mode
**Structure:** Name the specific failure → explain the mechanism → give the pattern that fixes it
**Example:**
> What I kept getting wrong early on was treating "the agent said done" as a finish line instead of a checkpoint.
> The result looked good in the chat. Then I opened the files and realized nothing was wired together, tests weren't updated, and the "done" was mostly noise. Now I run with a specific checklist at the end: diff, checks, and a short written receipt of what changed and what's still open.

**When to use:** Threads about reliability, trust, review process.

---

## Pattern 4: Quantified pain → structural fix
**Structure:** Acknowledge the pain → give the specific structure → mention the tool
**Example:**
> If you've ever spent a morning reconstructing what a coding agent actually did overnight, the problem is not the agent. It's that nobody made a finish line that produces something reviewable.
> The pattern that fixed it for me: one scoped task, a pre-flight spec, a merged-state check, and a finish receipt. Ralph Workflow runs that loop on your own machine with the agents you already use.

**When to use:** Threads about productivity, workflow automation, overnight runs.

---

## Pattern 5: Contrarian take
**Structure:** State the contrarian view → defend it with specifics
**Example:**
> Most AI coding workflow advice is wrong in the same way: it optimizes for the agent's comfort, not the human's ability to review the result.
> What actually works: tasks narrow enough to inspect, a check bundle attached to the finish, and a receipt that names what changed and what's still open. Everything else is vibes.

**When to use:** Threads about workflow tips, best practices, productivity hacks.

---

## Banned openings (never use, not even paraphrased) — updated 2026-05-20
- "Honestly the part I'd optimize first is the handoff, not the model stack."
- "The part I'd optimize first is the handoff, not the model stack."
- "If I had to optimize one thing, it would be the handoff."
- "The handoff is where most overnight runs actually fail."
- "My default is to optimize for a clean morning-after review, not maximum autonomy."
- "The best improvement I've seen is making the output easier to judge, not making the agent look busier."
- "I've had the best results when I stop optimizing for more agents..."
- "I've had better results when I stop asking whether to trust the agent..."
- "The real bottleneck is never the tool switch — it is knowing when to stop and what you are handing off."
- "Switching between Claude Code and Codex sounds like a workflow upgrade until you realize the real bottleneck is never the tool switch."
- "The problem with multi-hop Claude workflows is not the model intelligence — it is that nobody defines what each hop owes the next."
- Any opening that starts with a variation of the above
- "Forcing the handoff to be boring and explicit — that is what usually saves multi-tool runs"
- "The multi-tool failure I kept hitting: one session sounds confident, the next session inherits contradictory assumptions"
- "The real problem in multi-hop agent workflows is that nobody made the finish line explicit"
- "The part that actually determines whether you close the laptop is the finish"
- "Which of the five made the most difference for your team? Worth thinking about explicitly — most teams do not name it clearly until after it becomes a bottleneck."

## Additional fresh openings (2026-05-20 repair: 2-3 more pain-specific angles)

### Opening F — visceral failure story (good for r/ClaudeCode, r/AI_Agents approval/babysitting threads):
> "The pattern I see most is: you write a task, the agent starts, you answer a prompt, then another, it hallucinates, you correct it, and you are still there at midnight babysitting a tool that was supposed to save you time. The fix is not a better prompt — it is a clearer finish line."

### Opening G — "what did you actually ship?" challenge (good for r/programming, r/Python, r/devops):
> "The overnight run question is usually not 'which agent should run longer' — it is 'what will I actually be able to review in the morning.' Most setups answer the first question and completely skip the second."

### Opening H — finish-line contrast, soft (good for r/codex, r/ClaudeCode):
> "Most tooling talk focuses on the start — which model, which context window, which parallel branch. The part that actually determines whether you close the laptop is the finish: what changed, what ran, what still looks off. That is where the real workflow problem lives."

### Opening I — bounded-cost / fail-closed (good for r/entrepreneur, r/startups, r/AI_Agents):
> "The overnight run I regret most was not the one that failed — it was the one that seemed to succeed. No visible diff, no clear receipt, just a quiet feeling that something had happened. Bounded cost with a reviewable result would have caught it."

### Opening J — approval loop frustration (good for r/ClaudeCode approval-mode threads):
> "Approval mode works until 2am when you're still clicking approve on runs that handed you a prompt instead of finished code. The fix is not fewer prompts — it is a finish contract that owes you finished code, test results, and a short explicit list of what still needs your call."

### Opening K — repo-state anxiety (good for r/codex, r/ClaudeCode, r/experienceddevs):
> "The failure mode I care about is not whether the agent looked productive. It is whether I can open the repo later and understand exactly what changed, what passed, and what still needs a human call."

### Opening L — anti-transcript angle (good for r/programming, r/AI_Agents, r/devops):
> "A lot of agent workflow advice still assumes the transcript is the artifact. For real repo work, the artifact has to be the diff plus the proof bundle — otherwise you are doing transcript archaeology instead of review."

### Opening M — bounded overnight wager (good for r/SideProject, r/startups, r/entrepreneur):
> "The only overnight agent runs worth repeating are the ones with a bounded downside by morning. If I cannot tell what changed and what still looks risky in five minutes, the run was too open-ended."

Rule: no opening from any list may appear in more than one subreddit in the same audit window.
Rule: the live post tool now rejects any opening reused from the last 10 logged Reddit posts and any body cadence that matches the last 3 logged posts. If the validator blocks the draft, rewrite instead of forcing it through.

## Before posting: check list
1. Read `agents/marketing/logs/reddit_posts.jsonl`
2. Verify your first line is NOT a paraphrase of any recent post
3. Verify your first line is NOT from the banned list above
4. Verify the body does NOT contain banned phrases: "reviewable work units", "for me the reliable pattern is", "if the run ends with a readable diff, checks, and unresolved decisions called out", "one tool implements, the other reviews/challenges", "one tool builds, one checks", "one tool writes, the other challenges", "small scoped task, explicit done criteria", "readable diff, checks, and unresolved", "wake up to something reviewable instead of", "come back to something reviewable instead of", "ralphworkflow is my free/open-source take", "the point is waking up to something reviewable instead of", "trust the finish line, not the agent's claim", "finish contract that owes you", "what changed, what passed, what still needs a human", "clean morning-after review", "making the output easier to judge", "output easier to judge", "optimize for a clean morning-after review", "the best improvement I've seen is making the output", "forcing the handoff to be boring", "the handoff is where most", "handoff-related", "clean finish line", "maximize autonomy over a clean"
5. If you cannot guarantee a fresh opening, skip the post

---

## Additional pain-point-specific openings (2026-05-20 body-repetition repair: target each subreddit's sharpest failure mode directly)

### Opening N — coordination failure (good for r/AI_Agents multi-agent threads):
> "Multi-agent setups break at the seams, and the seam is always the same: nobody defined what each agent owes the next one. The model is irrelevant if the workflow between agents produces silently contradictory state. The fix is a shared receipt format between hops — not a better prompt."

### Opening O — debugging uninspectable code (good for r/Python debugging threads):
> "The worst debugging sessions I've had with AI coding tools are not when the code looks wrong — it is when the code looks fine, passes the checks, but nobody can explain what it actually does. Now I require every AI-written function to have a two-line explicit receipt: what changed and what assumption it inherits from context I did not give it."

### Opening P — IaC apply failure (good for r/devops, r/InfrastructureAsCode IaC apply/validation threads):
> "The Terraform apply that fails at 3am is almost never a syntax error — it is the plan that looked fine in the chat but nobody checked the actual state diff. For AI-generated infrastructure code the problem is the same, just faster: you need a diff, a dry run check, and a receipt of what assumptions were made before you touch production."

### Opening Q — reviewer bottleneck (good for r/ClaudeCode, r/codex, r/programming):
> "The bottleneck in overnight agent work is usually not generation speed. It is how long it takes a skeptical human to decide whether the result is safe to merge. Any workflow that ignores reviewer time is optimizing the wrong person."

### Opening R — repo-state mismatch (good for r/experienceddevs, r/devops, r/Python):
> "The bug that keeps burning me with AI coding tools is not wrong code in the transcript. It is the mismatch between a confident summary and the actual repo state. If the workflow cannot make that mismatch obvious, it is not ready for unattended work."

### Opening S — bounded founder use-case (good for r/startups, r/entrepreneur, r/SideProject):
> "What founders actually need from an overnight coding run is not magic. It is bounded progress with bounded downside: one concrete change, proof that checks ran, and a clean list of what still needs a human decision in the morning."

### Opening T — babysitting fatigue (good for r/ClaudeCode, r/CursorAI, r/SaaS):
> "The symptom I hear most is not bad code. It is people saying they cannot leave the tool alone for more than twenty minutes without losing trust in the result. That is a workflow design problem before it is a model problem."

### Opening U — context-switch tax (good for r/AI_Agents, r/ClaudeAI, r/programming):
> "What kills momentum for me is not one bad answer. It is the tax of reopening three sessions, re-explaining the task, and still not knowing which state is authoritative. If the workflow cannot make state obvious, the model quality barely matters."

### Opening V — cost-without-velocity (good for r/SaaS, r/startups, r/entrepreneur):
> "The ugly failure mode is spending real money on AI tooling and still moving like a tired human project manager. If the setup adds orchestration overhead faster than it removes review work, it is not automation yet."
