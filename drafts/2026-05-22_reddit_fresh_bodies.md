# Ralph Workflow — Fresh Reddit Bodies (Structural Rewrite v2)
Generated: 2026-05-22 05:10 CEST
Status: Fresh — uses new structural shapes, NOT the old 4-paragraph cadence

## Shape rules (from structural rewrite)
- Do NOT open with a general principle
- Open in the middle of a specific recognizable moment
- Do NOT end with the product definition — end with a question the reader answers
- Weave proof into the observation, not as a separate block
- Maximum 2-3 paragraphs
- One pain, one concrete observation, one honest close

---

## Draft A — Approval loop / review surface (approval-loop threads)
**Best for:** r/ClaudeCode, r/ClaudeAI — threads about approval loops, plan mode, review friction
**Shape:** Approval loop pain → why summary ≠ diff → Ralph close

**Body:**
You know the approval loop is broken when the summary looks fine but opening the diff makes you want to rewrite it.

The gap is usually not model intelligence. It is that the session ends with a confident summary instead of a bounded diff you can actually judge.

Ralph Workflow composes planning + implementation + verification into one workflow that tries to end with something you would actually merge. Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow

**Rules for this draft:**
- Read the thread first. Adjust the middle paragraph to reference the exact scenario described.
- Do not post if the thread is about a different pain (e.g., cost, speed).
- Do not post if someone has already mentioned workflow solutions.

---

## Draft B — Morning re-entry / overnight run (overnight/background-run threads)
**Best for:** r/AI_Agents, r/AgentsOfAI — threads about overnight runs, background agents, morning re-entry
**Shape:** Named pain → real question → Ralph close

**Body:**
The morning re-entry problem is real. You set up a long run, wake up to a wall of output, and spend an hour figuring out what actually changed.

The real question is not whether the agent finished. It is whether the result is bounded and reviewable at the point where you actually need to decide what to do next.

Ralph Workflow runs existing agents through a structured workflow that ends in a bounded diff and a clear review surface. Free and open source. Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow

**Rules for this draft:**
- Use only if the thread explicitly mentions overnight/background/long runs.
- Adjust the middle paragraph to match the specific overnight scenario described.
- Do not force this into threads about cost or model choice.

---

## Draft C — Done criteria / finish state (done-criteria threads)
**Best for:** r/ClaudeCode, r/codex — threads about how to know when a task is done, finish criteria
**Shape:** The evaluator's own question → why most answers are wrong → Ralph close

**Body:**
The question I keep asking after a long coding session is: what actually changed, and would I trust merging it?

Most agent outputs answer a different question — did the agent report success? That is not the same thing.

Ralph Workflow ends each workflow phase with a bounded diff and a review surface so the answer to "would I merge this?" is answerable. Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow

**Rules for this draft:**
- Works best in threads asking "how do you know when CC is done?" or "what's your done criteria?"
- The middle paragraph can be adjusted to reference the specific scenario in the thread.
- Do not post in threads about model quality or cost.

---

## Posting rules (mandatory)
1. Use these shapes ONLY. Do not regenerate old-cadenza bodies.
2. Read the thread before posting — adjust the middle paragraph to match the thread's exact scenario.
3. No thread match = no post. Do not force a fit.
4. Log each posted body in `agents/marketing/logs/reddit_posts.jsonl` noting which draft shape was used.
5. Do not post the same shape in more than 2 subreddits in the same window.

## Kill condition
If the next reddit_post_analysis still shows consistent 4-paragraph cadence, retire Reddit entirely and redirect to distribution ceiling work.
