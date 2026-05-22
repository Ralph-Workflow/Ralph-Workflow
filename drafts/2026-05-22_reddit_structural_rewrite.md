# Reddit Structural Rewrite — Ralph Workflow
Generated: 2026-05-22 03:45 CEST
Status: REPLACEMENT — breaks the old 4-paragraph cadence entirely

## Old cadence (BROKEN — never use again)
```
[Contrasting opener — "most agent setups fail because..."]
[Handoff/reviewer framing — "what I'd optimize is..."]
[Proof bundle — checks, diff, readable result]
[Product/link close — "Ralph Workflow does this..."]
```
This shape is confirmed across all 6 recent logged posts. Fresh openings do not fix it.

## New structural principles
- **Do not open with a general principle.** Open in the middle of a specific recognizable moment.
- **Do not end with the product definition.** End with a question the reader answers themselves.
- **Do not front-load the proof.** Weave proof into the observation, not as a separate block.
- **Maximum 2-3 paragraphs.** No 4-paragraph structure.
- **One pain, one concrete observation, one honest close.** Nothing else.

---

## Draft A — Approval loop pain
**Best for:** r/ClaudeCode, r/ClaudeAI
**Opening:** "You know the approval loop is broken when the summary looks fine but opening the actual diff makes you rewrite it."

Then: "The gap is usually not model intelligence. It is that the session ends with a confident summary instead of a bounded diff you can actually judge."

Then: "Ralph Workflow composes planning + implementation + verification into one workflow that tries to end with something you would actually merge. Primary repo is Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow"

**Why this shape works:** It opens on a visceral recognizable moment, not a principle. The proof is embedded as an observation. The product mention is last and minimal. No separate proof bundle block.

---

## Draft B — Morning re-entry pain
**Best for:** r/AI_Agents, r/AgentsOfAI
**Opening:** "The morning re-entry problem is real. You set up a long run, wake up to a wall of output, and spend an hour figuring out what actually changed."

Then: "The real question is not whether the agent finished. It is whether the result is bounded and reviewable at the point where you actually need to decide what to do next."

Then: "Ralph Workflow runs existing agents through a structured workflow that ends in a bounded diff and a clear review surface. Free and open source. Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow"

**Why this shape works:** Opens on a specific named pain ("morning re-entry problem"). Embeds the insight as the second paragraph's own observation, not as a proof bundle. Closes with the product in one sentence.

---

## Draft C — Done criteria pain
**Best for:** r/ClaudeCode, r/codex
**Opening:** "The question I keep asking after a long coding session is: what actually changed, and would I trust merging it?"

Then: "Most agent outputs answer a different question — did the agent report success? That is not the same thing."

Then: "Ralph Workflow ends each workflow phase with a bounded diff and a review surface so the answer to 'would I merge this?' is answerable. Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow"

**Why this shape works:** Opens with the evaluator's own internal question. No handoff framing. Proof is the insight itself. Minimal close.

---

## Posting rules (mandatory)
1. Only use these new shapes. Do not regenerate drafts that follow the old 4-paragraph cadence.
2. Do not post in more than 2 subreddits with the same draft shape.
3. Each post must be thread-specific — read the thread before posting and adjust the middle paragraph to match the exact thread question.
4. If no thread fits a draft shape genuinely, do not post.
5. After posting, log the opening line in `agents/marketing/logs/reddit_posts.jsonl` and note which draft shape was used.

## Kill condition
If the next reddit_post_analysis still shows a consistent paragraph cadence across posts, retire Reddit posting entirely and redirect effort to distribution ceiling work.
