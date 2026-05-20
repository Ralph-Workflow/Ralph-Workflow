# RalphWorkflow Reddit next-window fresh bodies

Prepared during cooldown on 2026-05-20 after re-reading:
- `agents/marketing/logs/reddit_posts.jsonl`
- `agents/marketing/REDDIT_LEARNINGS.md`
- `agents/marketing/reddit_fresh_openings.md`
- `seo-reports/reddit_monitor_2026-05-20_1220.md`

Guardrails used:
- no banned opening from `REDDIT_LEARNINGS.md` or `reddit_fresh_openings.md`
- no opening reused from the last 10 logged Reddit posts
- no default cadence of contrast opener -> handoff/reviewer framing -> proof bundle -> product-definition close
- site language bias: **no babysitting**, **finished code**, **tested code**, **ready to review**, **would you merge it?**

---

## 1) r/ClaudeCode — "Claude Code stuck in approval loop"
URL: https://www.reddit.com/r/ClaudeCode/comments/1tdai4f/claude_code_stuck_in_approval_loop/

**Draft body:**
Approval loops usually mean the tool is asking you to be the finish line.

What helped me was making approvals rarer but more meaningful:
- approve a bounded task, not an open-ended "keep going"
- require the run to come back with finished code, tested code, and one short note about anything still blocked on human judgment
- auto-stop the run when it hits an uncertainty it cannot prove through checks

That changes the question from "why is it asking me again?" to "did it return something ready to review?"

If every approval still feels like babysitting, the task is probably too wide or the stop condition is too fuzzy.

**Optional close if the thread still feels tool-receptive:**
That is the problem I care about in Ralph Workflow too: fewer approval nudges, more finished code you can judge quickly the next morning. Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow

---

## 2) r/ClaudeCode — "How do you ACTUALLY use CC+codex?"
URL: https://www.reddit.com/r/ClaudeCode/comments/1tb7j36/how_do_you_actually_use_cccodex/

**Draft body:**
I only bother using both when each one gets a narrow job that matches its strengths.

A practical split that has held up for me:
- use Claude first when the task is still under-shaped and you need constraints clarified
- use Codex for the bounded implementation pass once the target is concrete
- judge the result from the repo state afterward, not from whichever model sounded more convincing in chat

The useful test is boring: by the end, do you have code that is finished enough to inspect, tested enough to trust provisionally, and small enough that you would actually review it?

If not, adding a second tool probably just doubled the noise.

**Optional close if a product mention still feels native:**
That "separate the shaping from the bounded implementation, then inspect the actual repo result" loop is basically the space Ralph Workflow is trying to make repeatable on your own machine. Codeberg first: https://codeberg.org/RalphWorkflow/Ralph-Workflow

---

## 3) r/ClaudeCode — "How are you actually running Claude Code at scale on real codebases?"
URL: https://www.reddit.com/r/ClaudeCode/comments/1ta71n5/how_are_you_actually_running_claude_code_at_scale/

**Draft body:**
At scale, the first thing that breaks is not usually raw model quality. It is your ability to tell which changes are safe to merge and which ones need another pass.

The boring rules that helped most on larger repos:
- keep one branch/worktree tied to one concrete outcome
- treat migrations, auth, config, and shared contracts as explicit high-risk surfaces
- rerun checks on the would-be merged state, not just inside the branch bubble
- make every run leave behind a short human-readable summary of touched areas, checks run, and unresolved risk

That gives you a clean re-entry point when context gets messy.

Without that, "running at scale" mostly means generating more transcript to reconstruct later.

**Optional close if the thread clearly welcomes tools:**
That is also why Ralph Workflow is opinionated about the morning-after surface: the goal is not just longer runs, it is coming back to something you would actually merge. Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
