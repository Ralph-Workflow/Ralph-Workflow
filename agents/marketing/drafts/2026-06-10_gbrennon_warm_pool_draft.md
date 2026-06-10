# Draft: warm-pool Mom-Test 1:1 to Glauber Brennon (gbrennon)

> **Status:** PENDING_OWNER (D46). Do not post autonomously.
> **Source:** warm_pool.md (full pool, Codeberg star), ghrennon/odysseus = adjacent lane
> **Angle:** The odysseus self-hosted AI workspace is the surface where someone would
> naturally want unattended agent loops. Question: how does he handle the "agent runs
> the whole task itself" line today, in practice?

---

## Proposed public surface (operator's call on the exact placement)

**Option A — GitHub issue on `gbrennon/odysseus`:** open an issue in the SAME repo
where the relevant work lives. The angle is "I saw odysseus does <X> in the agent
lane; I am working on the same problem from a different angle (multi-day unattended
loop) and wanted to ask one question." Low-friction — issue text does the discovery.

**Option B — Reply on a recent push to a related repo (e.g. `gbrennon/neovim_experiments`
or `gbrennon/ModDar`):** NO. Those are not the lane; an off-topic comment is the
spammy move.

**Option C — Email:** NO. He is a Codeberg-only stargazer with no Apollo contact;
enrolling him in V8/V11 cold would be off-ICP. Email is the wrong surface here.

**Recommendation: Option A.** One issue on odysseus. Genuine engineering question
about the agent loop. Mom-Test voice. Polite-pass exit.

---

## Proposed issue body (Operator: edit/paste as needed before posting)

> **Title:** Question on the agent loop in odysseus (the opencode-backed one)
>
> Hi Glauber — I came across odysseus on Codeberg after seeing your star on
> Ralph-Workflow (an overnight-agent loop runner for Claude Code/Codex, local-first).
> I noticed odysseus's Agent mode is built on opencode + MCP + skills + memory —
> same architectural neighborhood as what I'm working on.
>
> One Mom-Test question, since you're actually shipping this:
>
> **When a user fires the Agent mode on a real task (not a demo), how do you decide
> when to let it keep running vs. when to interrupt it?** Specifically — do you
> have a time/cost/token budget the agent has to surface to the user, or is it
> "run until done or until the model starts looping"?
>
> The reason I'm asking: Ralph's whole premise is the *unattended* version of this —
> overnight, day-trip, multi-day, with check-in artifacts. I'm trying to learn
> whether the people already building in this space (you) have a clean answer for
> "when does the loop stop" or if that's still an open design problem.
>
> If it's a thoughtful question with a "yeah, here's what we do" answer, great —
> I'd genuinely like to know. If you're heads-down and not interested, totally
> fine, no follow-up needed.
>
> Repo if useful: https://codeberg.org/RalphWorkflow/Ralph-Workflow
> Thanks for shipping odysseus — the local-first + opencode foundation is the
> exact shape of where this space is going.

---

## Why this works (the marketing reasoning)

- **D37 (ICP grounded in converts):** gbrennon is in the warm pool (Codeberg star),
  AND he ships odysseus in the adjacent lane. The 1:1 is between two builders in
  the same neighborhood — not cold outbound.
- **Mom-Test:** the question is real (he is the only person I have read this month
  who is shipping a workspace where the agent-loop budget problem is the central
  UX problem). The question advances my learning, not my funnel.
- **D17 (warm-pool duty):** a Codeberg-only stargazer with 14y GH history, 112
  followers, 445 repos, and an active odysseus push. This is the warmest
  un-engaged Codeberg signal we have.
- **OUTREACH_COPY_CONTRACT:** no sign-off (mailbox would double-sign anyway in
  email, but this is GitHub — GitHub itself shows the username); no persona
  ("Elysia" / "marketer for"); no call/meeting ask; ONE repo link (the
  codeberg.org link, GitHub-native for a Codeberg-first user); ask ladder is
  reply → repo visit (implicit, via the link) → star (silent, organic).
- **D46 PUBLIC-WRITE CONDUCT:** this is a PENDING_OWNER draft, not an autonomous
  post. Operator decides whether to post on GitHub via their own session, with
  their own reputation on the line.

## What the loop will NOT do without operator consent

- Open this issue (gh-write-guard blocks it; D46 binds).
- Star/fork/watch gbrennon/odysseus.
- Reply to any reply that comes back.
- Add gbrennon to any Apollo sequence (he is not a verified contact; cold enrollment
  is off-ICP).

## Checkback schedule

- **+3d (2026-06-13):** if the operator posts the issue, check for a reply. If
  silent, no action (D17c-style escalation only applies to verified Apollo
  contacts, not warm-pool GitHub writes).
- **+7d (2026-06-17):** if still silent, log a `tactic="warm_pool_silent"` line
  to the ledger and treat the issue as an active presence (the issue text is
  itself a marketing artifact, even without a reply).
- **+14d (2026-06-24):** if the operator wants to escalate, the loop can draft a
  follow-up (different question, same repo). The loop will NOT auto-post it.

---

_Last loop action on this draft: 2026-06-10 22:18 GMT+2 — drafted from warm_pool.md
full-pool entry for gbrennon + the odysseus README read via gh API. No Apollo
changes, no public writes, no autonomous action._
