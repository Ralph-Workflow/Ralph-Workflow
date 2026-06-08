# MARKETING_PRINCIPLES.md — The playbook every marketing agent follows

**Read this BEFORE acting, every run.** This encodes marketing best practice for a **pre-revenue,
open-source** product. The instructions are deliberately step-by-step and literal: follow them exactly.

Ground truth right now: ~1,300 PyPI installs/month, **0.00% convert to a Codeberg star**, stars flat at
12 for 5+ weeks. So we HAVE acquisition (installs) but ZERO activation→advocacy. **The #1 job is
customer discovery + fixing activation, not making more content.**

---

## 0. The one rule that beats everything: TALK TO USERS (customer discovery)

Pre-revenue, you do not know your customer well enough. You must learn from real people, not guess.
This is **The Mom Test** (Rob Fitzgerald): ask about their life and past behavior, never pitch.

**Customer-discovery playbook (do this as a real action whenever possible):**
1. Find a real person who fits the audience (a GitHub issue author, a dev.to/Mastodon/HN commenter
   discussing AI coding agents, a Ralph installer who opened an issue, a Nightcrawler-adjacent builder).
2. Engage genuinely and ask about THEIR world (good questions — about the past, specific, not leading):
   - "What are you using to run coding agents unattended today, and where does it break?"
   - "Last time an AI agent botched a big task overnight, what happened? What did you do?"
   - "What would make you trust an agent to run while you sleep?"
   - "When you find a tool like this, what makes you star it vs. just try it and move on?"
3. NEVER pitch in a discovery conversation. Listen. The goal is to LEARN, not to convert.
4. **Log every insight** in `logs/customer_discovery.jsonl` (one JSON per insight: who, channel,
   the quote/pain, the job-to-be-done, what it implies for positioning/product). This is the most
   valuable data the loop produces. Re-read it before defining ICP or writing any message.
5. Patterns across ≥3 conversations → update the ICP (below) and the positioning emphasis.

---

## 1. The funnel (AARRR, adapted for open-source) — know which stage you're fixing

| Stage | OSS meaning | Our state | What moves it |
|---|---|---|---|
| **Awareness** | people learn Ralph exists | weak (no top-of-funnel) | genuine participation where builders are; one real conversation > 50 posts |
| **Acquisition** | they install (`pipx install`) | WORKING (~1,300/mo) | keep PyPI discoverable; not the bottleneck |
| **Activation** | first `ralph` run delivers a real win | **UNKNOWN — likely broken** | make first-run success fast & obvious; demo task; receipt |
| **Retention** | they use it again | unmeasured | reliability, the "walk away → verdict" promise actually holding |
| **Referral** | they star / tell others | **0.00% — THE LEAK** | a reason+moment to star at peak delight (post-run CLI nudge, repo/site CTA) |
| **Revenue** | n/a (free OSS) | — | advocacy IS our revenue: stars, contributors, word-of-mouth |

**Rule: fix the earliest broken stage first.** Ours is **Activation → Referral**. Do NOT pour effort
into Awareness volume while activation leaks — you'd acquire users who still won't star. Investigate
what happens AFTER install before scaling traffic.

## 2. ICP & JTBD (sharpen from discovery, never invent)

- **Working ICP hypothesis (refine with discovery data):** an experienced engineer or solo founder who
  runs coding agents (Claude Code / Codex / OpenCode), has real, well-specified work too big to babysit,
  and has been burned by agents failing unattended. They value local-first, free, reviewable output.
- **Job-to-be-done:** "When I have a big, specified task, help me hand it to my agents before I sleep and
  wake up to tested, reviewable code I can trust — without babysitting prompts."
- Every message must speak to THIS person and THIS job. If a message would appeal to "anyone," it's too
  vague — sharpen it. Update this section when ≥3 discovery insights point somewhere new.

## 3. Customer-acquisition principles (how to actually reach people — legitimately)

1. **Be genuinely useful first.** The only real win we have (Nightcrawler crediting Ralph) came from
   genuine usefulness reaching builders. Help first; mention Ralph only when it truly answers the need.
2. **Channel-message fit:** tailor every message to the channel + audience. dev.to = a real technical
   post; HN = substance, no marketing tone; Mastodon = conversational; GitHub = help in issues/discussions;
   email = one specific, personalized reason this curator/list cares.
3. **Human cadence, never spam:** 1–2 genuine actions/day, never templated, never the same text twice.
   (Spam/over-automation is what got Reddit shadowbanned. That's a permanent lesson.)
4. **Lead with the result** (per positioning): what the software did, what tests passed, what a human can
   verify — never logs/diffs.
5. **One conversation > fifty posts.** Depth and relationships beat volume. Follow up with people.

## 4. Activation fix (the real leverage — pre-revenue, this is everything)

The leak is install→star. Investigate and fix the post-install experience, in priority order:
1. **Find out what happens after `pip install`.** Read issues, ask installers in discovery: does the first
   `ralph` run deliver the promised "walk away → verdict"? Log findings in customer_discovery.jsonl.
2. **Make the first win fast and certain:** a clonable demo repo + pre-written PROMPT, a 5-minute "see it
   build & test this" quickstart, a real finish-receipt shown inline (don't hyperlink it away).
3. **Put the star ask at peak delight:** a post-successful-run CLI nudge ("⭐ if this saved you a night:
   <codeberg>") reaches the 1,300 installers where they actually are — the terminal — not the website.
4. **Give a REASON to star** (verbatim): "Ralph is free and runs locally — stars are the only signal we
   get that it's working for you, and they set what we build next."

## 5. Measurement & attribution (close the learning loop on the RIGHT metrics)

- Track AARRR metrics, not vanity: installs (acquisition), stars (referral), discovery conversations had,
  replies/engagement (awareness), backlinks. The PRIMARY is Codeberg stars; the leading indicators are
  discovery conversations and genuine engagements.
- Attribute every action: log it in `tactic_ledger.jsonl` with the expected signal + a checkback date,
  then score it against REAL movement. Double down on `worked`, kill `failing`, never repeat dead tactics.
- A tactic that doesn't move a real AARRR metric is `no_effect` no matter how much activity it produced.

## 6. The weekly rhythm (so the loop compounds)

- **Daily:** one genuine acquisition/discovery action on a viable channel + measure + ledger.
- **Every run:** read this file, customer_discovery.jsonl, the ledger, and adoption_metrics — then act.
- **Weekly:** synthesize discovery insights → update ICP/positioning emphasis; review the ledger → drop
  dead tactics, double down on winners; confirm you're fixing the earliest broken funnel stage.

---

**Bottom line for a pre-revenue OSS product:** customer acquisition + discovery is the whole game.
Talk to users, learn why 1,300 install and 0 star, fix activation, ask for the star at peak delight,
and reach new builders by being genuinely useful — one real conversation at a time. No spam, no evasion,
no theater, no handoffs. Decide and act.
