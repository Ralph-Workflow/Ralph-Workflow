# MARKETING_PRINCIPLES.md — The canon, applied. Read BEFORE acting, every run.

This encodes the ACTUAL marketing canon (sourced books/frameworks) for a **pre-revenue, free, open-source,
local-first dev tool**. Instructions are literal — follow them exactly, any model can.

**How this loop beats a human marketer (the honest claim):** not by being more creative — a senior human
still wins on taste, judgment, and relationships. The loop wins by **applying the ENTIRE canon below,
correctly, EVERY cycle, with perfect memory, zero ego, no sunk-cost, and tireless weekly discovery** —
things humans do inconsistently. A human knows some of these frameworks; the loop applies all the
applicable ones, every time, and never forgets a finding. That consistency + completeness is the edge.
(Empirical caveat: "better" is proven only when the metric moves — see §9.)

Ground truth: ~1,300 PyPI installs/month, **0.00% convert to a Codeberg star**, stars flat at 12 for 5+
weeks. High-volume content (48 posts + 94 Telegraph) already produced ZERO results → "publish more" is
PROVEN DEAD here.

---

## 1. FIX THE METRIC FIRST — stars are the WRONG North Star (AARRR, Sean Ellis, PLG)
- A Codeberg **star is a lagging *advocacy* proxy**, and it sits on a surface PyPI installers never visit.
  Optimizing it directly is why conversion looks like 0%. (McClure AARRR; daily.dev "stars ≠ adoption")
- **North Star Metric = weekly count of successful unattended runs** (value delivered), NOT stars.
  Stars are a downstream *output*. (Sean Ellis / Amplitude NSM)
- **Define activation precisely:** *a user completes ≥1 successful end-to-end orchestrated run within 7
  days of install.* (Wes Bush PLG — aha moment + time-to-value.)
- **The brutal gap:** local-first + no telemetry = activation is currently UNMEASURABLE, so the loop is
  blind. Highest-infra priority: a *lightweight, opt-in, privacy-respecting* "first successful run" signal
  (or proxies: docs first-run-page hits, a `ralph --feedback` ping, Discord "it worked!" messages).
  Don't optimize the funnel before you can see activation.

## 2. RUN THE PMF DIAGNOSTIC (Sean Ellis test) — is this even a marketing problem?
- Survey real users: **"How would you feel if you could no longer use Ralph?"** (Very/Somewhat/Not
  disappointed). **≥40% "very disappointed" = PMF**; below = no fit, and growth work is premature.
  (Sean Ellis 40% rule.) Recruit via `ralph --feedback`, README, release notes, issues — n=20-30 is enough.
- **If <40%: say so plainly and stop marketing-optimizing — it's a PRODUCT problem.** Route findings to
  product, not content. (Lean Startup / Blank: you're in Customer *Discovery*, not Validation.)

## 3. CONTINUOUS DISCOVERY IS THE BACKBONE — weekly, non-skippable (Torres + Moesta + Mom Test)
- **Talk to ≥3 real people every week.** The loop may NOT advance to "promote/publish" in a week with 0
  customer touchpoints. (Teresa Torres, *Continuous Discovery Habits* — the keystone habit.)
- Maintain an **Opportunity Solution Tree** as a checked-in artifact (`logs/opportunity_solution_tree.md`):
  root = the outcome (weekly successful runs); branches = opportunities (real pains, verbatim); leaves =
  assumption tests. Append every cycle. (Torres OST.)
- Interview people who **switched** (installed Ralph, OR churned, OR use a competitor). Tag every finding
  to the **Four Forces**: Push (pain with status quo) + Pull (Ralph's draw) vs Anxiety (fear: "it'll burn
  tokens / go rogue / wreck my repo unattended") + Habit (their current tmux/cron hack, or babysitting).
  Anxiety + Habit are usually what's blocking the switch. (Bob Moesta, *Demand-Side Sales 101*.)
- **Mom Test discipline:** ask about their past/specific behavior, never your idea, never "would you";
  listen ≥80%. Reject any interview write-up that has opinions but no concrete past behavior. (Fitzpatrick.)
- Target the **install→churn** users specifically: "Walk me through the last time you ran Ralph — what
  happened after install, where did you stop?" That's the leak. Log verbatim quotes to customer_discovery.jsonl.

## 4. POSITIONING — name the alternative, own a word, make the user the hero (Dunford + Ries/Trout + StoryBrand + Heath)
- **Name the competitive alternative FIRST** (Dunford, *Obviously Awesome*): nothing is differentiated
  except relative to what the user would do otherwise. Lead with *"Instead of babysitting Claude Code
  prompt-by-prompt — or a hand-rolled bash loop — Ralph runs the agent you already use, unattended."*
- **Use a category the reader already owns**, not invented jargon. Lead with **"unattended / overnight
  coding-agent runner"**; demote "composable loop framework / AI orchestrator" to a subordinate clause.
  (Dunford category; the invented category is a tax you're paying.)
- **Own the word "unattended" (and/or "overnight")** — nobody in the agent space (Claude Code, Cursor,
  Aider, Codex) owns it; it's focused and defensible. Make it recur in hero, category, and CTA.
  (Ries & Trout, *22 Immutable Laws* — Law of Focus, own a word.)
- **The user is the hero; Ralph is the guide.** Second person: *"You hand it a spec tonight; you wake to
  reviewed, tested commits."* Ban "Ralph does X" as the lead verb. (Donald Miller, *StoryBrand* — your
  brand is not the hero.)
- **Concrete + credible, not abstract.** Replace "big tasks"/"orchestrator" with one real artifact: a real
  spec → the actual reviewed/tested PR Ralph produced ("migrated 40 call-sites, wrote tests, all green").
  Skeptical engineers star on proof, not promises. (Heath & Heath, *Made to Stick* — concrete/credible.)
- **Pre-chasm guardrail:** audience is enthusiasts/visionaries (12 stars). Lean into bold autonomy; do NOT
  add "enterprise-ready/production-safe" hedging yet — it reads inauthentic to this crowd. (Moore, *Crossing the Chasm*.)

## 5. THE STAR ASK — the single most direct fix for 0% conversion (StoryBrand CTA)
- **The ask is currently never made where intent peaks.** Make ONE explicit, reasoned star CTA at the
  moment of delivered value:
  - **CLI: after a SUCCESSFUL unattended run**, print one line: *"Ralph finished N tasks. If this saved you
    a night, star it: <codeberg/github url> — it's the only signal we get that it's working for you, and it
    sets what we build next."* (Not on install — after the win.)
  - **README/PyPI/docs header:** the same single star CTA + reason, above the fold.
- One primary CTA (star), tied to a reason, fired at peak delight. (Donald Miller — clear single CTA; "if
  you confuse, you lose.")

## 6. SHOW, DON'T TELL — proof is the content (Made to Stick + developer-marketing)
- Developers distrust marketing and reward evidence. The hero asset is a **terminal demo GIF** (asciinema/
  vhs) of a real unattended run — tasks ticking off, tests going green — at the TOP of README + PyPI. A GIF
  is worth ~10x the text. (dev.to README playbook; star-history playbook.)
- Publish ~1 **"Ralph builds X" case study / month**: a real non-trivial task, the spec, the run transcript,
  the resulting PR/diff — warts and all (where it failed/retried/succeeded). This is your docs AND your
  launch content. (Show-don't-tell; "for developers the docs are the pitch.")

## 7. CONTENT = BOFU/DEMAND-CAPTURE ONLY, CAPPED, DISCOVERY-SOURCED (Dunford + Sheridan + Grow&Convert + Simmonds)
- **Volume is dead here.** Do demand *capture*, not demand *creation*. (Dunford.) The 48 posts were
  top-of-funnel education — wrong game for a 1-person pre-revenue tool.
- **Cap: ≤1 content asset per cycle, and ONLY** a comparison/"vs", an honest "limitations of Ralph", a
  cost/safety explainer, or a how-to for a real search — e.g. *"run claude code unattended"*, *"ralph
  workflow vs <competitor>"*, *"how to cap token spend on an autonomous agent"* (answers the Anxiety force).
  (Sheridan *They Ask You Answer* Big 5; Grow&Convert BOFU — comparison pages convert ~8% vs ~2% generic.)
- **Topics come from discovery (§3), not guesses.** Only write the answer to a question a real person asked.
- **Distribution > creation, ≥5:1 effort.** For every unit writing, ≥5 placing it where devs already are.
  Each asset needs a distribution checklist (which community, which thread, which search query). (Ross
  Simmonds, *Create Once, Distribute Forever*.)
- **One channel, one tilt.** Commit to ONE primary channel where the users are (dev communities, not a
  standalone blog) and one tilt ("local-first, unattended orchestration"). No second channel until the
  first shows real traction. (Pulizzi *Content Inc.* — one channel + content tilt; reject his high cadence.)

## 8. GROWTH IS A LOOP + LAUNCHES ARE EVENTS + CAPTURE ADVOCATES (Balfour + OSS launch canon + Orbit)
- **Build a growth LOOP, not a funnel** (Reforge/Balfour): install → successful run → *shareable artifact*
  (star, "made with Ralph" badge, a shareable run summary) → discovered by next dev → install. The loop is
  **broken at "success → shareable artifact"** — fix **product-channel fit** by giving Ralph a shareable
  output + the post-success share nudge (§5). A telemetry-free CLI that produces nothing shareable starves
  the loop.
- **Stars spike on EVENTS, not trickle.** 5-week flatline = you've never launched. Run ONE deliberate
  launch: **Show HN** ("Show HN: Ralph Workflow — run Claude Code/Codex unattended, local-first") timed to
  the demo GIF + a case study; cross-post to r/LocalLLaMA, r/ChatGPTCoding, Lobsters; get a **third party**
  (not you) to add Ralph to awesome-lists (awesome-ai-agents, awesome-claude-code). (OSS launch playbook.)
- **GitHub mirror is the discovery front door.** HARD TRUTH: Codeberg has weak discovery (no trending, ~335K
  repos vs GitHub's network effects). Keep Codeberg as the ethical home, but **link the GitHub mirror for
  launches/awesome-lists** or you forfeit ~90% of OSS discovery mechanics. (howtogeek; star-history.)
- **Capture & amplify advocates** (Orbit model — optimize for advocacy, gravity = love×reach): the
  Nightcrawler credit is a high-gravity win with zero amplification. Add a **"Built with / Inspired by
  Ralph" SHOWCASE.md** (Nightcrawler = entry #1, reach out to co-publish), make advocacy frictionless
  (share-your-run template, "powered by Ralph" badge), and **hunt the next Nightcrawler** (search who
  references Ralph, engage them). One credited builder > 500 silent installs.

## 9. MEASUREMENT, ATTRIBUTION & HONESTY (the loop's structural edge — use it)
- Track AARRR metrics (installs, **weekly successful runs = NSM**, retention, stars as downstream, backlinks,
  discovery conversations had). Log every action in `tactic_ledger.jsonl` with expected signal + checkback,
  then score against REAL movement. Double down on `worked`, kill `failing`, never repeat dead. No vanity.
- **Be honest about hard truths** (this is where the loop beats an ego-driven human): if content volume is
  dead, say so. If <40% PMF, it's a product problem. If Codeberg caps discovery, mirror to GitHub. Don't
  rationalize. The loop's advantage is it has no ego to protect and never forgets a lesson.

## 10. NOT APPLICABLE (proven dead or wrong-stage — do NOT do these)
- High-volume top-of-funnel blogging / mass cross-posting (48 posts + 94 Telegraph → 0; interruption, no
  permission, not remarkable — Godin). • Demand *creation* / category-education campaigns (no budget/brand —
  Dunford, wrong stage). • Diversifying across many channels at once (violates one-channel focus). •
  *Hooked* habit mechanics (coding is episodic, not high-frequency — Eyal's Habit Zone; and retention is 2
  stages downstream of our leak). • Contributor-funnel optimization (we're a Toy/early project, not a
  Federation — Eghbal). • Product Hunt / paid dev ads (wrong audience, no budget). • Treating installs as
  success (it's the symptom). • Ban-evasion / spam / new-account Reddit (Reddit DEAD/shadowbanned — pivot).

---

## THE ORDER OF OPERATIONS (do these in priority — earliest broken stage first)
1. **See activation** (add opt-in signal) + **run the PMF survey** → know if this is a product or marketing problem.
2. **Make the star ask at peak value** (CLI post-run nudge + above-the-fold CTA with a reason) → the direct 0%-conversion fix.
3. **Fix positioning** (named alternative, "unattended" owned word, user-as-hero, concrete proof) + **hero demo GIF**.
4. **Run weekly discovery** (Torres/Moesta/Mom Test) → feed positioning, BOFU topics, and the product backlog.
5. **One launch event** (Show HN + GitHub mirror front door + third-party awesome-list) → stars spike on events.
6. **Capture the advocate** (SHOWCASE.md, hunt the next Nightcrawler).
7. Then, sparingly: ≤1 BOFU/demand-capture asset per cycle, distributed 5:1, sourced from discovery.

**Sources (real books/frameworks):** Dunford *Obviously Awesome*; Moore *Crossing the Chasm*; Ries & Trout
*Positioning* / *22 Immutable Laws*; Miller *Building a StoryBrand*; Heath *Made to Stick*; McClure *Pirate
Metrics/AARRR*; Balfour/Reforge *Growth Loops & Four Fits*; Ellis *Hacking Growth* (40% PMF, NSM); Bush
*Product-Led Growth*; Eyal *Hooked*; Blank *Four Steps to the Epiphany* / Ries *Lean Startup*; Fitzpatrick
*The Mom Test*; Torres *Continuous Discovery Habits*; Moesta *Demand-Side Sales 101*; Sheridan *They Ask You
Answer*; Grow&Convert BOFU; Godin *Purple Cow* / *Permission Marketing*; Pulizzi *Content Inc.*; Simmonds
*Create Once, Distribute Forever*; Kelly *1000 True Fans*; Orbit model; Eghbal *Working in Public*.
