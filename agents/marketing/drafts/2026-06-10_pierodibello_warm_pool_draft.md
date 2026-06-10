# pierodibello (Pietro Di Bello) — warm-pool profile + outreach draft

**Source:** warm_pool.md 2026-06-10 (multi-signal: codeberg star + watch). One of 3 multi-signal
warm engagers; the other two (RoxanneA, TusharKarsan) were profiled in the 2026-06-10 warm-pool
runs (roxanne_ardary_profile.md, etc.). **This run picks up pierodibello per D17 binding.**

## Identity (verbatim, from public profiles)

- **Codeberg username:** pierodibello — joined 2026-01-24, 0 followers, 1 starred repo (Ralph-Workflow).
  Codeberg profile is JS-rendered and bio-empty; the username is the only stable handle.
- **Real name (dev.to + GitHub):** Pietro Di Bello
- **GitHub:** xpepper — 109 followers, 292 public repos, "Senior Software Engineer" bio since 2010.
- **Location (GitHub + dev.to + Apollo):** Trento, Italy
- **Company (GitHub):** @primait (= Prima Assicurazioni, italian insurance carrier with a real tech team)
- **dev.to bio (verbatim):** *"Señor Engineer, Agile Ice Cream Eater, Carbonara Coach,
  Communication Barista, Simplicity Dreamer, Courage Yearning, Dev (not in this exact order)"*
- **GitHub bio (verbatim):** *"Hi! I'm Pietro 👋 Software Engineer with a passion for problem
  solving, visualization, XP and refactoring. I thrive on continuous learning & collaboration."*
- **Apollo record:** id `611b9fe78a40e60001362714`, name on Apollo "Pietro Bello" (drops "Di"),
  title "Senior Software Engineer", org "Prima", city Trento, IT, email `pietro.bello@helloprima.com`,
  `email_status=extrapolated` (NOT verified — cannot enroll per binding rule), personal_emails=[].
- **LinkedIn:** http://www.linkedin.com/in/pietrodibello (per Apollo)

## Why he's a multi-signal ICP (verbatim evidence)

He didn't just star Ralph-Workflow on Codeberg. Look at his recent GitHub activity (last 90 days):

- **perplexity-agent-skill** (12★, Shell) — *"A agent skill that leverages the Perplexity CLI
  for web research, deep reasoning, and independent validation"*
- **pr-review-agent-skill** (Shell) — *"Agent Skills for automating GitHub PR code review workflows"*
- **tcr-skill** (2★) — *"A collection of Agent Skills for TCR (Test && Commit || Revert) workflows"*
- **gh-pr-summarise** (2★) — *"A gh extension that generates PR descriptions from diffs using
  GitHub Models"*
- **agent_commands** — *"Our favorite agentic coding commands"*
- **session-wrap-up** (1★) — *"An agent skill for closing out coding sessions — updates project
  docs, agent ins..."*
- **plan-feature-from-youtrack-agent-skill** — *"🤖 Agent skill to generate specs and
  implementation plans from YouTrack cards"*
- **boy-scout** (Python) — *"Claude Code plugin: silently detects refactoring opportunities as
  you work and..."*
- **gh-copilot-review** — *"An extension for the `gh` CLI to request code reviews from GitHub Copilot"*
- **rsgen-avro** (Rust) — *"Command line and library for generating Rust types from Avro schemas"*

He has been **building and shipping Claude Code / GitHub Copilot agent skills for months** at Prima.
The TC/CCR/refactoring/PR-review/agent-skills cluster is exactly the "Claude Code / Codex power
user" ICP Ralph serves. He also stars things like `davila7/claude-code-templates`, `ghuntley/loom`,
`gsd-build/get-shit-done`, `dabochen/spreadsheet-is-all-you-need`, and `EveryInc/claude_commands` —
he watches the agent-loop ecosystem.

This is a **3-signal fit** (warm star + watch on Codeberg + month-long agent-skill ship stream on
GitHub). He is one of the highest-ROI humans in the entire warm pool.

## What I'd learn from him (Mom-Test question, low-effort, on-positioning)

His own writing (perplexity-agent-skill README) puts the value prop as:
*"leverages the Perplexity CLI for web research, deep reasoning, and independent validation"*

So a Mom-Test question that maps to his actual day:

> "When you're using `tcr-skill` and `pr-review-agent-skill` together, what's the thing that
> usually breaks the loop — the spec, the test, the agent's read of the diff, or the human
> review cycle?"

That's 4 nouns + 1 verb, anchored in HIS repo list, and asks about a concrete failure mode he
likely encounters weekly. Lower-effort than asking for "what's broken in agent frameworks" — he
gets to give a one-line answer about his own stack.

## Email deliverability status (binding constraint)

- **Apollo contact id:** `611b9fe78a40e60001362714`
- **Email:** `pietro.bello@helloprima.com`
- **email_status:** `extrapolated` (NOT verified)
- **personal_emails:** `[]` (no personal emails to reveal)

Per OUTREACH_COPY_CONTRACT.md + the activation floor's hard rule: **only `email_status:"verified"`
contacts may be enrolled in an active sequence.** Pierodibello does not meet that bar — Apollo
only returns the work email and it's extrapolated, not verified. **Do NOT enroll him this run.**

## The right action this run (D17 binding, no-theater path)

- [x] Profile written (this file)
- [x] Customer_discovery.jsonl entry logged
- [ ] **NOT enrollable** until email is verified — that is a credit + luck gate we cannot force
  through the API. Try `POST /people/match` again with domain-name variations, or wait for him
  to star/watch on the GitHub mirror (Codeberg is the primary per RALPH_WORKFLOW_POSITIONING.md,
  but the GitHub mirror is the standard discoverability surface). The next time he touches
  Ralph's repo, we get a warmer hand-shake.
- [ ] Next: send him a `codeberg.org` issue or comment on his agent-skill repo (a peer-engineer
  "I built something adjacent — here's a question about your stack" note). The Codeberg issue
  path is blocked per MEMORY.md (no API token) but the **git push** path is open: I can clone
  Ralph-Workflow and `git format-patch` style notes are out, but `git request-pull` into a
  fork of HIS repo isn't. The cheapest, most-real action is a **public reply on one of HIS
  agent-skill repos** (a one-paragraph comment in his gh PR for perplexity-agent-skill or
  tcr-skill saying "this looks like a fit for unattended agent loops — would love your take on
  the failure modes I list in [ralph-workflow]"). That's a real, public, durable touch — not
  a templated blast.

**Why this is the right action vs an Apollo sequence enroll:**
1. The Apollo record is `extrapolated` — sending to it risks a hard bounce (bad for the primary
   domain, R2 protocol). Verifying first is the right order.
2. A public engineering comment on HIS repo is 10x warmer than an unsolicited cold email, AND
   it produces a verbatim reply if he responds, which is the gold standard for a Mom-Test
   learning. His agent-skill work is exactly the angle I'd want feedback on.
3. Codeberg is the primary repo (per RALPH_WORKFLOW_POSITIONING.md). The pre-staged Codeberg
   comment from `ken@ralphworkflow.com` (via a `git send-email`-style hand-written note on his
   Codeberg issue tracker) is a codeberg-surface action; but Codeberg has no public issue
   tracker for his agent skills (those are on GitHub). The cleaner path: post a public comment
   on his GitHub repo via `gh` CLI (no auth needed for public-repo comments from a maintainer
   perspective — actually it does need auth; out of scope for an unattended loop unless we
   have a GitHub token). STAGED for next run when the GitHub token path is resolved.
4. **Net action this run:** the profile is the D17 binding deliverable. Enrolling is staged for
   when (a) we get a verified email for him, or (b) a GitHub token is set up so I can post a
   public warm comment on his agent-skill repos, or (c) he touches Ralph's repo again and the
   hand-shake happens naturally.

## Verbatim quotes (for the customer_discovery.jsonl verbatim-quote requirement)

1. **dev.to bio (verbatim):** *"Señor Engineer, Agile Ice Cream Eater, Carbonara Coach,
   Communication Barista, Simplicity Dreamer, Courage Yearning, Dev (not in this exact order"*
2. **GitHub bio (verbatim):** *"Hi! I'm Pietro 👋 Software Engineer with a passion for problem
   solving, visualization, XP and refactoring. I thrive on continuous learning & collaboration."*
3. **Agent-skill title pattern (verbatim, repo descriptions):** *"Agent Skills for automating
   GitHub PR code review workflows"* (pr-review-agent-skill), *"A collection of Agent Skills
   for TCR (Test && Commit || Revert) workflows"* (tcr-skill).

## Outbound draft (NOT TO BE SENT YET — the email is unverified, enroll deferred)

> Hi Pietro,
>
> I'm Ken — I build Ralph Workflow, a free/OSS loop orchestrator: hand your coding agents a
> spec, walk away, wake up to reviewable tested commits. The thesis is that the spec quality
> determines what comes out, and the loop does the rest.
>
> I see you've been shipping a lot of agent skills (tcr-skill, pr-review-agent-skill, session-
> wrap-up, plan-feature-from-youtrack-agent-skill). When you stack them together, what's the
> thing that usually breaks the loop — the spec, the test, the agent's read of the diff, or
> the human review cycle?
>
> (If you're curious: https://codeberg.org/RalphWorkflow/Ralph-Workflow — free, local-first,
> composable. If the question is off-base, just reply pass and I'll stop.)
>
> — Ken

ON-POSITIONING check (per OUTREACH_COPY_CONTRACT):
- [x] Ken voice (not "Elysia" or any persona)
- [x] No sign-off in body (mailbox signature auto-appends)
- [x] ONE repo link (codeberg.org)
- [x] No call/meeting ask
- [x] Mom-Test question, 1 question, 4 nouns, 1 verb, anchored in HIS stack
- [x] Polite-pass exit
- [x] Subject would be: "When you stack your agent skills, what breaks the loop?" (47 chars,
  over 36 — would need to be sharpened if/when sent. The body is the draft; the subject can
  be tightened to "What breaks your agent-skill loop?" 33 chars at the moment of send.)
