# Reddit Fresh Openings — 2026-05-20

Diverse, pain-specific opening lines. One per subreddit archetype.
Do NOT reuse any opening across subreddits.

## r/ClaudeCode — Approval/Babysitting Pain

**Opening A (frustration-first):**
"If you find yourself re-reading a Claude Code approval prompt at 11pm and wondering whether you actually approved anything useful, that is a workflow design problem — not a model problem."

**Opening B (metric-first):**
"The fastest way to know if a Claude Code session was worth it: open the diff and run the checks in under five minutes. Most sessions fail that test before the agent ever stops."

**Opening C (structural):**
"The approval loop only saves time if the run ends with something you can grade in two minutes. If it ends with another prompt, the workflow just moved the work downstream."

## r/AI_Agents — Multi-Agent Chaos Pain

**Opening A (failure-mode-first):**
"Multi-agent setups usually break not at generation but at the handoff: one session leaves vague edits, the next inherits confusion and spends cycles reconstructing intent."

**Opening B (specificity-first):**
"When two AI agents touch the same repo without a shared handoff contract, you do not get the best of both — you get the intersection of their assumptions, quietly compounded."

**Opening C (consequence-first):**
"The pain with agent teams is rarely the individual outputs. It is the merged state afterward: who validated the combined result, and what did they actually check?"

## r/ClaudeAI — Autonomy/Trust Pain

**Opening A (definition-first):**
"The real question for autonomous agent runs is not whether it can run unattended — it is what it should hand back when it stops."

**Opening B (failure-first):**
"The autonomy failure I run into most is not the agent doing something obviously wrong. It is the agent confidently doing the wrong thing and stopping as if it were finished."

**Opening C (contrast-first):**
"Finished code, passed checks, and a short unresolved list — that is what trustworthy unattended output looks like. Anything less is just a longer prompt in disguise."

## r/LocalLLaMA / r/MachineLearning — Engineering Workflow Pain

**Opening A (outcome-first):**
"What separates an overnight run that was worth it from one that just made the morning more complicated is usually the spec written before it started, not the model inside it."

**Opening B (specific-hypothesis-first):**
"Hypothesis: most AI coding workflow failures are not capability failures — they are handoff failures, where nobody defined what the next phase should receive."

**Opening C (principle-first):**
"One bounded diff, one check bundle, one short receipt of unresolved decisions. That is the finish standard that makes unattended runs reviewable instead of just long."

## r/Python / r/devops — Scripting/Automation Pain

**Opening A (automation-gap-first):**
"The gap between 'I automated this' and 'this actually saved me time' is usually the review step — if the output is not bounded and checkable, automation just moves the work around."

**Opening B (specific-tool-first):**
"If you are running Claude Code or Codex on real engineering tasks and the session ends with a confident paragraph instead of a diff you can actually inspect, the workflow needs a tighter finish contract."

**Opening C (pattern-first):**
"The automation pattern that actually holds up: spec before, receipt after. What changed, what passed, what still needs judgment. Everything else is just a longer script."

## Generic / Low-Fit Subreddits

**Opening A (constraint-first):**
"The constraint that usually fixes an AI coding workflow is not more agents — it is tighter handoff between phases: what changed, what ran, what still needs a human decision."

**Opening B (metric-first):**
"The single metric that tells you if an agentic workflow is working: can you open the result tomorrow morning and make a call in under five minutes?"

**Opening C (contrast-first):**
"Agents that produce long transcripts and agents that produce reviewable diffs look similar during the run. They are completely different by the morning."

---

## Usage Rules

- Pick one opening per subreddit archetype per audit window
- Never reuse the same opening across two different subreddits in the same window
- Rotate through the full set before cycling back
- Track used openings in `reddit_posts.jsonl` — do not repeat within 30 days
