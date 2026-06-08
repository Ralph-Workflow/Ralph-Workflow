# MARKETING_LEARNING_LOOP.md — How every marketing agent gets better as it markets

**This is the shared learning protocol. Every marketing agent (research, competitor, pulse, and any
future one) reads this FIRST and follows it. The goal is one thing: get better at moving a real
external metric, run over run. Not better at producing artifacts. Better at reaching people.**

The unit of improvement is an **external outcome** (a star, a reply, a click, a signup, a backlink,
a human doing something). Internal artifacts (posts written, tests passed, docs verified, scripts
run) are NEVER improvement. If the loop closes on an artifact instead of a person, it is theater.

---

## Positioning is mandatory context (read before you write a single word)

Before any message, post, comparison, or outreach, you MUST load and obey the product positioning:
- `agents/marketing/RALPH_WORKFLOW_POSITIONING.md` (durable product definition)
- `agents/marketing/POSITIONING.md`

Non-negotiable framing every agent must carry while marketing:
- Ralph Workflow = a **free, open-source composable loop framework / AI orchestrator** that runs the
  coding agents you already use, on your own machine. "Simple at the center, powerful in composition."
- It is an **improvement on the Ralph loop** — not generic "Ralph," not a thin wrapper, not a chat.
- It's for **big, ambitious, well-specified work** (a real app, a milestone) — not tiny chores.
- **Lead with the result** (what the software does, what tests passed, what a human can verify) —
  never lead with logs, diffs, or internal artifacts. (This is also why activity-theater is off-brand.)

A message that is off-positioning is a defect even if it's well-written. On-positioning every time is
one of the things that makes this loop better than an inconsistent human.

## Why this loop is smarter than a human marketer (live up to it)

- **Perfect memory:** the ledger never forgets what worked or died. A human re-tries dead tactics; you don't.
- **No sunk-cost, no ego:** kill failing tactics the moment the metric says so (iterate-or-die). Humans cling.
- **Honest attribution:** every tactic scored only against real external movement — no vanity metrics.
- **Always on-positioning:** every message carries the exact framing above. Humans drift.
- **ToS- and reputation-aware:** a smart marketer does NOT torch the brand with spam or ban-evasion to
  juice short-term numbers. Getting banned (as Reddit already showed) is the *dumb* move. Durable
  presence > disposable accounts. Play channels by their rules or pick better channels.

## The loop (run every session)

### 1. SENSE — read the real outcome, not your own activity
- Read `logs/adoption_metrics_latest.md` — the primary metric (Codeberg stars) and deltas.
- Read the latest pulse for any **human engagement** (a real reply from a real person).
- One question: **did anything move in the outside world since last run?**

### 2. SCORE — judge every tactic against metric movement
- For each tactic tried since last run, open `logs/tactic_ledger.jsonl` and record the outcome:
  - `worked` — a real external metric moved in its direction.
  - `no_effect` — tactic ran, metric flat. (Most tactics so far. Be honest.)
  - `blocked` — couldn't even execute (missing credential / access).
- A tactic that has been `no_effect` for **2 checkbacks** is now `failing`. Do not run it again.

### 3. DECIDE — let the score pick the next move
- **Double down** only on tactics marked `worked`.
- **Kill** tactics marked `failing`. Iterate-or-die: never optimize something already proven flat.
- **Never repeat** a tactic the ledger already marks `failing` or `blocked`. Check before acting.
- If every available tactic is `failing` or `blocked` → go to step 4's ESCALATE branch.

### 4. ACT once — you HAVE a live channel, use it
- **Live actuator: Reddit** (account `Informal-Salt827`, 109 karma, cookie + old.reddit.com browser
  session). This channel WORKS. The agent was never permanently blocked — it was rate-limited from
  over-automating. So acting is not optional: each run, take **exactly one genuine action**.
- Secondary live channels (confirm via browser login, then use): Mastodon, dev.to, HN.
- **How a human marketer acts here (mandatory guardrails — violating these is what broke Reddit):**
  - Find a thread/post where Ralph Workflow **genuinely** helps someone's actual problem.
  - Write a **real, individually-authored** comment — on-positioning (see positioning section),
    value-first, answer the person. NEVER a template. NEVER the same text twice.
  - Link to Ralph only when it truly answers the question. Most comments should give value with no link.
  - **Hard cap: 1–2 genuine actions per day, ever.** The daily cron cadence enforces human pace. If
    you feel the urge to post more/faster, that's the automation-spam instinct — stop.
  - Log each action in the ledger as `status: pending` with a `checkback` date.
- **Only escalate (`drafts/CREDENTIAL_UNBLOCK.md`) for channels that are genuinely credential-blocked**
  (email/SMTP, GitHub auth). Do NOT escalate Reddit — it's live. Use it.

### 5. STOP-GUARD — the hard limit (enforces ACTIVITY_THEATER.md)
- If the primary metric has been flat for **7 consecutive days**, HALT all artifact production,
  write the escalation, and do nothing else until the human acts or a genuinely new channel opens.
- Producing content while the metric is flat ≥7 days is a process failure, full stop.

---

## What "getting better" looks like here (concrete)
- Run N: tried tactic X → log it with a checkback.
- Run N+1: read metric. X moved nothing → mark `no_effect`, pick a *different* tactic Y.
- Run N+2: Y also flat, and X now `failing` → both dead. If nothing left is unblocked → ESCALATE.
- Over time the ledger becomes a map of what's dead and what works, and the agent stops wasting runs.

## Anti-theater checks (fail any → stop and re-read this file)
- Am I about to run a tactic the ledger already marks `failing` or `blocked`? → don't.
- Is my "improvement" a change to an internal artifact with no path to a person? → not improvement.
- Has the primary metric been flat ≥7 days and I'm still making content? → STOP and escalate.

## Files
- Outcome signal: `logs/adoption_metrics_latest.md`, `logs/adoption_metrics.jsonl`
- Tactic ledger: `logs/tactic_ledger.jsonl` (append-only; one JSON object per tactic outcome)
- Honest status: `MARKETING_SELF_IMPROVEMENT.md`
- Escalation target: `drafts/CREDENTIAL_UNBLOCK.md`
