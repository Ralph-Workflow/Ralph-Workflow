# RalphWorkflow Reddit next-window packet - 2026-05-19 15:27 CEST

Live posting is **not** the move right now.

Latest watchdog state references report `reddit_monitor_2026-05-19_1520.md`.
- `status: fresh_opportunity_rate_limited`
- `detail: opportunity_state:fresh_rate_limited; opportunities:7`

So this packet is optimized for the **next safe window**, not for forcing a post now.

Use these only if the next watchdog check clears the rate window and the thread still looks fresh enough.

Messaging guardrails preserved in every draft:
- what it is: Ralph Workflow is a free and open-source tool that orchestrates the coding agents you already use on your own machine
- who it is for: developers doing engineering work too big to babysit and too risky to trust blindly
- why different: it is about a reviewable finish state, not just more agent sessions
- why now: you can use it tonight for overnight work and wake up to something you can actually review

---

## 1) `r/ClaudeCode` - Claude Code just shipped a "run until done" mode. Upgrade to v2.1.139 for /goal.
- URL: <https://www.reddit.com/r/ClaudeCode/comments/1taty8a/claude_code_just_shipped_a_run_until_done_mode/>
- Mention fit: medium-low
- Best landing page to seed:
  - https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/START_HERE.md
- Why this stays in the packet:
  - still usable as a workflow reply without forcing a product pitch
  - landing page keeps the CTA on a concrete first-use or proof path
  - best RalphWorkflow angle from the monitor: run-until-done only helps if done is bounded, fail-closed, and easy to review

### Draft body A
The most useful constraint I added to my agent workflow was not more prompts. It was a finish-state definition.

Agents are not good at knowing when to stop. Humans are not good at reviewing a transcript. The gap closes when the run produces a bounded diff, real checks, and a short summary of open questions — not just a claim that the job is done.

What that looks like in practice:
https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/START_HERE.md

Ralph Workflow is free and open-source: uses the agent tools you already run on your own machine, and tries to make the output something you can actually evaluate instead of another transcript.

---

## Posting discipline before using any of these
1. Re-read the last 3 logged Reddit bodies first.
2. Re-run `python3 agents/marketing/reddit_watchdog.py` before posting; do not trust this file alone on timing.
3. If the thread shifted, rewrite the opener instead of forcing the draft.
4. If the reply is useful without Ralph, keep Ralph secondary.
5. Use one seeded proof/comparison link only - no link pile.
6. Prefer only one post in the next safe window unless a second thread is clearly exceptional.
7. Reject any draft that falls back into the same cadence as the last three bodies.
