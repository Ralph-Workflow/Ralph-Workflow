# LinkedIn DM Template — pre-staged pivot (R6 escalation, Day 14/21)

> **Purpose:** When the Apollo email channel stalls (0/9 replies by Day 21 per R6), pivot to
> 1:1 LinkedIn DMs. This template is pre-staged during the silent-email-experiment window so the
> pivot is one-click activation, not a multi-hour rewrite. Drafted 2026-06-09 22:25 by EVALUATOR
> (the prompt's R6 rule was edited in the same turn to require this pre-staging).
>
> **Channel ownership:** LinkedIn outreach is NOT in this Apollo agent's primary toolkit (the
> Apollo toolkit is email sequences + contact reveal). LinkedIn is the explicit PIVOT channel
> declared in R6 when the email channel is wrong. This template is a real-DM, founder-typed
> artifact — no automation, no connection-blast tooling, no templated sequences.

---

## On-contract self-check (run before sending)

- [x] NO sign-off in the body (the LinkedIn profile banner is the implicit signature, and the
  "— Ken" / "— Ken Li" sign-off patterns are explicitly banned by OUTREACH_COPY_CONTRACT §1)
- [x] Zero call/meeting/demo asks (Mom-Test question only)
- [x] Exactly one repo link present
- [x] ≤ 6 sentences, one question, no banned phrases (no Elysia, no "marketer for", no
  calendly, no "60-second", no "coffee chat")
- [x] On-positioning per RALPH_WORKFLOW_POSITIONING.md (result-first, named alternative,
  user-as-hero, founder-voice)

## Channel-specific shape (LinkedIn DMs differ from email)

- **Length cap:** 4-5 sentences max (LinkedIn DMs truncate in mobile preview past ~3 lines)
- **First-line hook:** must reference THEIR specific work (not Ralph). The connection reason is
  what they see first; "saw your post on X" or "noticed your [org]'s open-source approach" beats
  "I'm building X, would love to connect" every time.
- **Repo link:** ONE link only. For LinkedIn-native audiences, the GitHub mirror
  (github.com/Ralph-Workflow/Ralph-Workflow) reads better than codeberg.org as a first link —
  most LinkedIn OSPMs will recognize the GitHub URL faster. (The OSPM email sequences used the
  GitHub link already; keep consistency.)
- **Polite-pass exit:** "if the question is off-base, no worries — happy to drop it" (DMs have
  no "reply pass" convention, so the phrasing has to acknowledge they can simply not reply).
- **No CTA past the question.** A LinkedIn DM that ends with "would love to set up a call" is
  a sales DM. The question is the ask; the response is the metric.

## The template (4-line Mom-Test, ON-POSITIONING)

> [First line: 1 sentence referencing THEIR specific work — must be researched, not templated.
> Example: "Saw your [talk / post / repo / release] on [topic] — [specific detail that proves I
> read it, not skimmmed]."]
>
> I build [Ralph Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow) — a small open-source
> loop orchestrator so coding agents can run overnight and wake you up to reviewable commits.
>
> One thing I'd genuinely value your take on: [Mom-Test question, the same one used in the email
> sequences for this angle — see agents/marketing/logs/customer_discovery.jsonl for the angle
> that worked best when the email experiment was live].
>
> If the question is off-base, no worries — happy to drop it.

## Personalization hooks per angle (the Mom-Test question rotates)

| Angle | First-line hook sources | Mom-Test question |
|---|---|---|
| OSPM @ AI/devtools (#18) | Recent OSS launch, contributor-day talk, AI/devtools org blog post | "When your community picks up a new dev tool, what's the path that actually gets it installed — peer mention, channel post, error-driven search, something else?" |
| OSPM @ observability (#20) | Recent observability blog, conference talk, OSS release | "How do you usually find out about a new dev tool — direct from a maintainer you trust, a community channel, or just error-driven search?" |
| DevRel/Founding (#21) | Recent post on building DevRel from scratch, conference talk, framework launch | "For founding DevRel at a framework — what's the discovery loop that actually lands a new dev tool, vs the ones that just get noise?" |
| Platform/DX engineer (#23, V6) | Recent internal-devtools blog post, conference talk, build-vs-buy post | "When your team picks up a new agent tool, what makes the difference between 'tried it once' and 'we actually use it' — and what kills the second case?" |

## Why this template is on-contract

1. **WHO SPEAKS** — Ken, founder-voice, no persona, no "marketer for" frame, no "— Ken" sign-off
2. **WHAT WE ASK FOR** — ONE Mom-Test question, ONE repo link, no call/meeting/demo, polite-pass
   exit acknowledged (the "no worries" line is the LinkedIn equivalent of "reply pass")
3. **SHAPE** — 4 sentences, one question, no banned phrases, ≤ 50-char subject equivalent
   (LinkedIn DMs have no subject, so the first line functions as the hook)
4. **MESSAGE LAYER** — names the alternative implicitly ("small open-source loop orchestrator so
   coding agents can run overnight" is the user-as-hero result-first frame from
   RALPH_WORKFLOW_POSITIONING.md)

## Anti-theater guard

- Do NOT send this template to anyone whose email is verified on Apollo (use the email channel
  for them — LinkedIn is the pivot WHEN the email channel is provably flat at 0/9)
- Do NOT send this template to more than 1-2 people/day (the 1-2 sends/day cap applies across
  all channels; the Day 14/21 pivot is a CHANNEL change, not a VOLUME change)
- Do NOT add a CTA past the question. The temptation to "if interested, here's a 15-min slot"
  is the 2026-06-09 R3 mistake exactly — resist.
- Do NOT use a connection request with a blank note. The note IS the first line of the DM,
  and the blank note is a red flag in 2026 LinkedIn norms.

## Activation checklist (when R6 fires at Day 14/21)

1. Confirm the in-flight email experiment is provably flat (0/<n> replies across all 5
   Ralph-AB sequences, with ≥2 delivered per sequence, Day 21 elapsed since V1 first delivery)
2. Update icp_findings.md §6 to reflect the channel-pivot decision
3. Identify the angle that performed best in the email experiment (the one with the highest
   click rate, even if 0 replies — clicks are the strongest open-MPP-resilient signal)
4. Use the matching row in the personalization hooks table above for that angle
5. Send 1-2 DMs/day, founder-typed (no automation, no browser-extensions, no Sales Navigator
   sequences). LinkedIn rate-limits DMs aggressively — 5-10/week is the practical cap.
6. Log every send to tactic_ledger.jsonl with tactic="linkedin_dm_pivot" so the next
   evaluation can attribute the result.

---

Drafted 2026-06-09 22:25 GMT+2 by the Apollo EVALUATOR as a pre-staging artifact for the
Day 14/21 LinkedIn-pivot path declared in apollo_marketer_prompt.md R6.
