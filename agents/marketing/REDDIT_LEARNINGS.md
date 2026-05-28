# Reddit Learnings

Use this as required context before drafting, recommending, or posting anything on Reddit for RalphWorkflow.

## What worked
- Community-first replies work better than product-first posts.
- Plain language works better than jargon-heavy "AI orchestration" wording.
- Practical advice about real pain points is the safest angle.
- A light mention of RalphWorkflow can fit if the post is useful without it.
- Old Reddit comment forms are a reliable fallback when the new Reddit editor is hard to automate.

## What did not work
- Narrow scans produce weak opportunities and bad selection.
- Obvious test-post language makes the account look robotic.
- Overexplaining the system in abstract terms makes the post sound promotional.
- Browserless/headless Reddit flows are unreliable and trigger blocks.
- Repeating the same opening across different subreddits — detectable as templatespam.

## ⚠️ Openings to never reuse (2026-05-19)
The following opening has been used verbatim across multiple subreddits and is now stale:
> "I've had the best results when I stop optimizing for more agents and start optimizing for reviewable work units."

This phrase or its close variants must NOT be used as a comment opener. If you are drafting a new comment and catch yourself reaching for this phrasing, pivot immediately to one of the fresh openings below.

## Fresh opening templates (use one per subreddit, rotate)
Use these as starting points — adapt to the specific thread, but do not reuse the same opening across subreddits.

**Opening A — scope/review angle (good for r/ClaudeCode, r/AItools):**
> "What I kept getting wrong early on was treating 'the agent said it was done' as the same thing as 'the job is actually done.' The distinction matters most when you come back to the result the next morning."

**Opening B — phase boundaries angle (good for r/programming, r/Python, r/devops):**
> "The part that bites me most is not choosing which tool to run — it is deciding who reviews what the tool produced. Phase boundaries matter more than model choice in any multi-agent setup."

**Opening C — drift/trust angle (good for r/entrepreneur, r/startups, r/webdev):**
> "The overnight run problem is usually not the agent — it is the lack of a clear stop condition. Without a bounded definition of done, you wake up to either nothing or something you cannot trust."

**Opening D — mixed-tool handoff (good for r/codex, r/ClaudeCode):**
> "Switching between Claude Code and Codex sounds like a workflow upgrade until you realize the real bottleneck is never the tool switch — it is knowing when to stop and what you are handing off."

Rule: no opening from this list may appear in more than one subreddit in the same audit window.

## Tone rules
- Use simple, concrete language.
- Match the updated landing page tone at https://ralphworkflow.com.
- Prefer "walk away and come back to something reviewable" over technical buzzwords.
- Avoid sounding like a launch post unless the thread clearly asks for products/tools.

## Posting rules
- Do not post on a fixed quota just to fill space.
- Try to find 5-10 credible opportunities per day when they genuinely exist.
- Every post/comment must be worth reading even without RalphWorkflow mentioned.
- RalphWorkflow must stay secondary to the value of the post.
- If the opportunity is weak, skip it.

## Review loop
Before recommending or posting:
1. Review recent outreach history in `outreach-log.md`.
2. Review the latest entries in this file.
3. Note what worked, what failed, and what to change.
4. Carry those lessons into the next draft.

## Update rule
After each Reddit monitoring pass or live post:
- append what worked
- append what failed
- append any wording, community, or tone lessons
- keep this file concise and practical

## New lessons - 2026-05-16
### What worked
- Threads about trust, overnight drift, remote supervision, and worktrees are the best fit for RalphWorkflow.
- The strongest angle is simple: help people walk away and come back to something reviewable.
- Skeptical threads about multi-agent setups are often better than hype threads because they invite practical workflow advice.

### What did not work
- Announcement threads and product showcase threads are usually too noisy or too promotional to be worth forcing.
- "AI orchestration" wording is weaker than plain talk about review, stop conditions, and morning-after results.
- Direct Reddit fetching from this host is still unreliable, so the monitoring loop must tolerate partial visibility.

### Wording and selection lessons
- Prefer "reviewable diff," "clear done criteria," "worktree/branch isolation," and "walk away safely."
- Avoid leading with RalphWorkflow unless the thread already asks for tools.
- A good opportunity should contain a real unresolved workflow pain, not just tool excitement.
- Prefer fresh question-led threads over launch/showcase threads.
- Score opportunities on whether the reply is still useful with no product mention at all.
- Remote control alone is not the pitch; the stronger angle is coming back to a clean result you can review quickly.
- Worktrees are a good wedge, but the fuller value is scope control + checks + reviewable finish.
- Fresh question-led threads about trust, approval stalls, overnight drift, and reviewability are stronger than announcement or showcase threads.
- Score every opportunity on whether the reply is genuinely useful with no product mention at all.
- Remote/mobile-control threads are strong market signal, but often weaker outreach targets than plain workflow pain threads.
- If RalphWorkflow is mentioned, it should usually appear as a light closing note rather than the center of the reply.
- Fresh, question-led threads from the last 0-3 days are usually better than announcement threads, showcase posts, or launch threads.
- Remote-control and approval-loop threads are strong market signal, but often weaker outreach targets than threads about trust, review, and morning-after results.
- The best replies use simple workflow language: plan, run in isolation, check, review.
- Direct workflow questions about approval loops, handoff structure, and review roles are stronger than broad "which tool is better?" debates.
- Worktree env/bootstrap pain is real market signal, but many of those threads want practical setup help, so product mentions should usually stay out.
- Add a commentability filter after search: unresolved workflow pain, explicit ask for process advice, and useful even with no product mention.

## New lessons - 2026-05-16 (19:15 CEST)
### What worked
- Fresh Claude Code/Codex workflow-question threads are still the cleanest RalphWorkflow opportunities.
- Threads asking how to use both tools together are better than generic comparison debates because they invite practical handoff advice.
- The strongest helpful frame remains: one tool builds, the other checks, and the result only counts when the diff is small and reviewable.

### What did not work
- Remote-control and launch threads continue to generate market signal, but they are weaker comment targets than open workflow questions.
- Broad "agent orchestration" searches still surface too much promo noise unless they are filtered down to workflow pain.
- Good opportunities can still fail operationally at posting time, so shortlist quality and posting reliability need to be tracked separately.

### Wording and selection lessons
- Prefer wording like **use both**, **workflow**, **critique**, **reviewable diff**, **clear stop conditions**, and **small scoped task**.
- Position RalphWorkflow around safer unattended work and cleaner review, not around "more agents."
- Give extra weight to threads where the answer is obviously useful with no product mention at all, then decide later whether a light closing mention fits.
- Add a final commentability check: if the thread does not contain a real open workflow question, it is probably not worth forcing even if the topic matches.

## New lessons - 2026-05-16 (09:17 CEST)
### What worked
- Threads about overnight drift, review loops, and trust are stronger than generic remote-control or launch threads.
- The best current openings are pain-led questions where a checklist-style answer helps even with no product mention.
- Claude Code + Codex handoff threads are a strong fit when the advice stays boring and operational: small scope, second-opinion review, clean diff.

### What did not work
- Remote-control announcement threads keep producing signal but are often weak outreach targets because they fill up with product chatter fast.
- Worktree-only threads are not enough on their own unless the discussion also touches scope drift, invalidated assumptions, or review friction.
- Comparison threads about "which model is better" are usually weaker than threads about how people actually review and trust the output.

### Wording and selection lessons
- Prefer: "reviewable finish," "final check bundle," "small scoped task," "acceptance criteria," and "morning-after result."
- Avoid framing RalphWorkflow as "more agents" or "just remote control." The stronger frame is safer unattended work with a clear review path.
- Score candidates on two simple questions: is the pain real, and is the reply useful even if RalphWorkflow is never named?

## New lessons - 2026-05-16 (14:15 CEST)
### What worked
- Fresh threads explicitly asking for **workflow critique** or **how to work with both Claude Code and Codex** are stronger than generic "which tool is better?" threads.
- `r/ClaudeCode` and `r/codex` are currently the best places to look for RalphWorkflow-fit discussions.
- Overnight success-story threads can still be good opportunities when the reply focuses on why the run stayed reviewable.

### What did not work
- Generic remote-control and launch threads still produce signal, but they are weaker outreach targets than direct workflow-question threads.
- Broad agent/orchestration keywords surface too many promo-heavy or low-fit results.
- Comparison threads without a real workflow pain usually flatten into preference debates and are not worth forcing.

### Wording and selection lessons
- Prioritize search/query words like **workflow**, **critique**, **overnight**, **trust**, and **how to work with both**.
- The best replies stay boring: scope, checks, handoff, diff, review.
- If RalphWorkflow is mentioned, it should usually appear as a light closing note after the useful advice is already complete.

## New lessons - 2026-05-16 (20:08 CEST)
### What worked
- Merge-safety and review-loop threads are strong because they expose the limit of worktrees alone: people still need a clean final check before merge.
- Approval/draft-state discussions are good RalphWorkflow fits when the pain is clearly about trust, not just feature comparison.
- Fresh troubleshooting threads about worktree friction can still be good opportunities when the answer stays practical and boring.

### What did not work
- Pure showcase or "my tool does multi-agent" posts still attract too much product chatter to be safe outreach targets.
- Threads that are mostly vendor preference debates keep drifting away from workflow advice.
- Worktree threads without an open question or unresolved pain are weaker than they first look.

### Wording and selection lessons
- Prefer wording like **final merge check**, **review bundle**, **draft state**, **semantic conflicts**, and **clean re-entry point**.
- Distinguish **worktree isolation** from **finished-result trust**; that gap is where RalphWorkflow fits best.
- If the thread is really asking for troubleshooting help, keep RalphWorkflow unmentioned unless the value case is unusually natural.
- Review the actual logged comment bodies, not just titles and notes, before drafting the next Reddit reply.
- Avoid reusing the same opening line or near-identical body structure across multiple comments, even when the thread theme is similar.
- Treat pre-switch `u/Clear-Past7954` posts as historical context only; future optimization should focus on what works from `u/Informal-Salt827`.

## New lessons - 2026-05-16 (22:30 CEST)
### What worked
- Direct workflow-question threads in `r/ClaudeCode` and `r/codex` are still the cleanest RalphWorkflow opportunities.
- The strongest replies stay close to the site language: too big to babysit, too risky to trust blindly, and come back to something reviewable.
- Merge-safety, approval-loop, and Claude/Codex handoff threads reveal stronger buying intent than generic multi-agent chatter.

### What did not work
- Repeating the same body skeleton makes otherwise good advice feel canned.
- Leading with a polished thesis line is getting stale; it reads less like a thread reply and more like a prepared promo block.
- Worktree/setup threads are useful research, but often weak outreach unless there is a real open process question.

### Wording and selection lessons
- Vary the opening shape on every post: question, agreement, concrete example, or short caution instead of the same thesis-led intro.
- Do not keep ending with the same "we wrapped this into RalphWorkflow, but the structure matters more than the tool" pattern.
- Prefer simple phrases from the site and market: **reviewable diff**, **proof it holds up**, **clean re-entry point**, **approval loop**, **use both**, **small scoped task**.
- Add a repeat-pattern check before any post: compare against the last 3 logged bodies for opening line, paragraph order, and closing mention.
- Add a commentability check after search: unresolved workflow pain, explicit ask for process advice, and useful even with no product mention.

## New lessons - 2026-05-17 (09:00 CEST)
### What worked
- The strongest durable message is still simple: walk away, come back to a reviewable result, and judge it like a PR.
- Trust, merge safety, approval loops, and Claude/Codex handoff structure remain better angles than broad "multi-agent" talk.
- `r/ClaudeCode` and `r/codex` still look like the best outreach communities when the thread is a real workflow question.

### What did not work
- The current reply set is too structurally repetitive: thesis opener, "for me the reliable pattern/version is," then a RalphWorkflow closing.
- Reusing the same four-paragraph skeleton across accounts makes the comments feel prepared instead of native to the thread.
- Repeating a brand-softening closing still creates pattern risk even when the exact wording changes a little.

### Wording and structure lessons
- Ban the repeated opener **"I've had the best results when I stop optimizing for more agents and start optimizing for reviewable work units."** until it is no longer recent.
- Ban the repeated closer pattern **"We've wrapped that pattern into RalphWorkflow, but honestly..."** for the next batch of Reddit writing.
- Before posting, compare the draft against the last 3 logged bodies for: opening line, paragraph count, paragraph order, and whether the product mention lands in the same final-slot shape.
- Prefer thread-native openings: answer the OP directly, name the concrete pain in their post, or share one short example instead of opening with a polished thesis.
- If a product mention fits, vary its placement or skip it entirely; do not default to a soft brand mention in the last paragraph.
- Keep using site-matching plain language: **too big to babysit**, **too risky to trust blindly**, **reviewable result**, **clean diff**, **proof it holds up**.
- Treat older `u/Clear-Past7954` comments as background only; future optimization should focus on `u/Informal-Salt827` patterns and freshness.

## New lessons - 2026-05-17 (09:15 CEST)
### What worked
- Fresh workflow-question threads about **merge safety**, **approval friction**, and **Claude/Codex review handoffs** are still the best RalphWorkflow opportunities.
- Threads that already say **"I can't trust this unattended"** or **"I need a review step"** map cleanly to the site without forcing jargon.
- The strongest helpful angle remains boring and concrete: small scope, independent review, merged-state checks, and a clean morning-after diff.

### What did not work
- Worktree setup/env threads are strong market signal, but many are better for research than for mentioning RalphWorkflow.
- Product/showcase posts about orchestration keep surfacing, but they are weaker than plain process-pain threads.
- Previous Reddit bodies are now repetitive beyond wording alone; the full shape repeats too often: thesis opener -> "reliable pattern" paragraph -> worktree/review paragraph -> soft RalphWorkflow close.

### Wording and selection lessons
- Give extra weight to threads where the OP explicitly wants a **review loop**, **approval state**, or **merge-safe finish**, not just more parallelism.
- Add a repeat-pattern check against the last 3 full logged bodies for **opening move**, **paragraph sequence**, and **where/if the product gets mentioned**.
- If the best answer is just practical setup advice, skip the product mention entirely.
- Prefer site-matching plain language like **reviewable diff**, **reviewable result**, **proof it holds up**, **too risky to trust blindly**, and **clean re-entry point**.
- Keep a stronger reject filter for posts that are interesting market signal but weak comment targets: launch threads, showcase threads, and worktree-env troubleshooting with no broader workflow question.

## New lessons - 2026-05-17 (12:15 CEST)
### What worked
- Approval-state and review-trust threads are still strong RalphWorkflow fits when they ask for process help, not just product opinions.
- `r/ClaudeCode` and `r/codex` still outperform broader AI communities for clear workflow pain.
- Filtering out threads already used in prior RalphWorkflow outreach improves shortlist quality immediately.

### What did not work
- Older high-fit threads keep lingering in search results and can crowd out fresher opportunities if freshness is not weighted harder.
- Broad "which tool is better?" debates still create more heat than useful room for a grounded reply.
- The repeat-pattern risk is now about full body cadence, not just reused phrases.

### Wording and selection lessons
- Add a **prior-use gate** before drafting: if RalphWorkflow already commented there, do not re-shortlist it just because the topic still fits.
- Add a **freshness gate**: older threads can stay as research signal, but they should be downgraded as live outreach targets unless activity is still moving.
- Approval friction is becoming a clearer pain than generic multi-agent talk; prefer threads about **draft state**, **review step**, **approval loop**, and **giant end-of-run review pain**.
- Ban the default body cadence of thesis opener -> reliable pattern -> worktree/review paragraph -> soft product close.
- If a product mention fits, vary its placement or skip it entirely; do not default to the final paragraph.

## New lessons - 2026-05-18 (09:05 CEST)
### What worked
- The strongest plain-language market words are still **babysitting**, **overnight**, **review in the morning**, **what changed**, and **one place to see what happened**.
- Review/reconstruction pain is now sharper than generic trust talk; good threads ask what changed, what broke first, what is safe to merge, or how to recover cleanly.
- Threads about Claude Code + Codex handoff, worktree mess, and visible finish state still map best to RalphWorkflow when the answer stays practical.

### What did not work
- Generic multi-agent/dashboard hype is weaker than concrete workflow pain, even when the category signal is real.
- Repeating the same proof bundle shape in every reply still creates pattern risk, even with fresher wording.
- GitHub/product CTA habit should not be automatic; recent Reddit posting volume has not yet shown clear adoption lift from always ending there.

### Wording and selection lessons
- Prefer site-matching plain language: **no babysitting**, **review-ready code**, **what changed**, **tests already ran**, **one place to see what happened**, **would you merge it?**
- Add a stronger **reconstruction + commentability filter**: only shortlist threads where the reply is useful with no product mention and the OP has a real unresolved workflow pain.
- Before posting, compare against the last 3 logged bodies for **concept cadence**, not just opener/closer reuse; avoid falling back to scope -> checks -> diff -> receipt in the same rhythm every time.
- Treat Reddit as selective distribution, not quota work: same-day workflow-question threads are worth acting on; weak-fit threads should be skipped.

## New lessons - 2026-05-18 (09:15 CEST)
### What worked
- The strongest remaining fits are still handoff-state and morning-after review threads, not broad multi-agent hype.
- Site-matching plain language still works best: **no babysitting**, **what changed**, **would you merge it?**, **reviewable result**.
- Separating **helpful reply fit** from **RalphWorkflow mention fit** makes the shortlist cleaner.

### What did not work
- Search is now crowded with already-used Reddit threads, aging trust threads, and setup/help posts that are worth answering but weak for product mention.
- Forcing a 5-10 RalphWorkflow-fit quota on a saturated search pool lowers quality.
- Worktree/setup friction is real signal, but many of those threads should stay product-free.

### Wording and selection lessons
- Add a two-stage filter: first ask whether the thread is worth replying to with no product mention, then ask whether a light RalphWorkflow mention would still feel native.
- Do not count setup/help threads toward the RalphWorkflow opportunity target unless they clearly widen into trust, handoff, or review-state pain.
- Keep rejecting prior-used threads early and keep checking the last 3 logged bodies for **concept cadence**, not just repeated phrases.

## New lessons - 2026-05-18 (12:15 CEST)
### What worked
- Unused threads about **handoff contracts**, **cleanup / checkpoint noise**, and **bounded unattended runs** are still the best remaining RalphWorkflow-fit research pool.
- Site-matching plain language still works best: **no babysitting**, **what changed**, **tests already ran**, **would you merge it?**
- Separating **helpful reply fit** from **mention fit** continues to keep the shortlist honest.

### What did not work
- Search saturation is still getting worse: many strong topical threads are already used, aging out, or better left product-free.
- Setup/help threads around worktrees and preview envs are useful signal, but they are often weak RalphWorkflow mention targets.
- Repetition risk still comes from the same **concept cadence**, even when the wording is shorter or fresher.

### Wording and selection lessons
- Add a **cleanup / handoff-surface filter**: prefer threads asking what changed, what passed, what to merge, what to clean up, or how to re-enter safely.
- Keep a product mention out when the thread is really asking for tactical git/worktree help.
- Before posting, compare against the last 3 logged bodies for **opening move**, **concept cadence**, and **product-mention placement**.
- Treat Reddit as selective distribution, not quota work: if only 2-3 current threads are true RalphWorkflow mention fits, say that clearly.

## New lessons — 2026-05-18 (15:15 CEST)
### What worked
- Threads about **cleanup noise**, **visible finish state**, and **bounded autonomy** still map best to RalphWorkflow when the answer stays plain and useful first.
- Re-reading full prior comment bodies continues to catch repetition that title-only review misses.
- The site's plain-language frame still fits best: **no babysitting**, **what changed**, **would you merge it?**, **reviewable result**.

### What did not work
- The search pool is now saturated enough that 5–10 shortlist-worthy threads can exist while only 2–3 are real RalphWorkflow mention fits.
- Worktree/setup threads keep surfacing as strong market signal but weak outreach targets.
- Repetition risk still comes from **body logic rhythm** as much as wording reuse.

### Wording and selection lessons
- Keep a three-gate shortlist: **helpful reply first**, **mention fit second**, **cleanup / handoff surface third**.
- Prefer threads about **what changed**, **what passed**, **what to merge**, **what to clean up**, or **how to re-enter safely** over generic tool-choice debates.
- Keep product mentions out of tactical git/worktree/help threads unless the workflow-value case is unusually natural.
- Before posting, compare against the last 3 logged bodies for **opening move**, **paragraph order**, **concept cadence**, and **where the product mention lands**.

## Site language additions — 2026-05-18 (16:15 CEST)
Reviewing live ralphworkflow.com against REDDIT_LEARNINGS shows the site has sharpened some marketing language that is not yet reflected in learnings. The direction is unchanged, but these specific phrases should be adopted in future Reddit drafting:

- **"finishes the job"** — stronger than "reviewable result" alone; direct promise of completion
- **"Plan → Build → Verify"** — named three-phase flow; useful shorthand when describing the architecture
- **"Other AI tools give you a start. Ralph Workflow gives you a finish."** — the comparison frame; effective in trust/review threads
- **"Start the job and close the laptop"** — visceral "no babysitting" variant; captures the unattended promise in four words
- **"What you can ship tonight"** — action-oriented "tonight" framing; tighter than "first finished result tonight"
- **"Install in under five minutes — first finished result ready tonight"** — speed + overnight promise combined

Site positioning is consistent with May 17 findings: core pain (babysitting, midnight sessions), three-phase flow, PR-review framing, and "would you merge it?" evaluation all intact. No directional shift — only refinement.

## Site language update — 2026-05-18 (22:15 CEST)
Second review tonight confirms May 18 16:15 findings are live. Additionally observed:

- **New problem statement block** — "Sound familiar? You write a task. The AI starts. You answer a prompt. Then another. It hallucinates. You correct it. You're still there at midnight babysitting a tool that was supposed to save you time." — more visceral failure framing than prior versions; good for skeptical or pain-led threads.
- **"What you can ship tonight" examples** — nine specific task types (feature, testing, refactor, internal tool, cleanup, API, documentation, migration, bug fix) now listed on the page; concrete and actionable framing worth mirroring.
- **Three phases now numbered and explicit** — Sharpen → Build/verify/fix → Land reviewable result. The "Plan → Build → Verify" shorthand from the earlier update is now confirmed live with numbered phases.
- **"Ralph Workflow does not replace your AI tool — it gives it a clearer finish line"** — useful clarification that counters the "another tool to learn" objection.

No directional change. Core positioning, three-phase flow, overnight promise, PR-review framing, and "would you merge it?" evaluation remain intact and consistent with all prior learnings.

## New lessons - 2026-05-19 (07:17 CEST)
### What worked
- Approval-drag and remote-supervision threads are still useful RalphWorkflow research because they expose finish-state trust pain in plain language.
- Re-reading the full logged post bodies still catches repeat-pattern risk that title-only review misses.
- The site language still fits best when it stays simple: **no babysitting**, **start the job and close the laptop**, **what changed**, **would you merge it?**

### What did not work
- Prior-use saturation is now severe enough that the same high-fit search results keep resurfacing after they have already been used.
- Comparison and setup threads are often worth replying to, but weak places to mention RalphWorkflow.
- Shorter comments can still feel repetitive when they fall back to the same **contrast opener -> builder/reviewer split -> finish proof -> link** shape.

### Wording and selection lessons
- Run a strict **prior-use gate** before freshness scoring so already-used threads do not keep crowding the shortlist.
- Keep counting **credible discussion opportunities** separately from **credible RalphWorkflow mention fits**; the first number can still be 5-10 while the second is only 1-3.
- Approval-mode, remote-supervision, and worktree-help threads should only count as RalphWorkflow opportunities when the pain clearly widens into **visible finish state**, **review surface**, or **morning-after trust**.
- Before posting, compare against the last 3 logged bodies for **opening move**, **concept cadence**, **builder/reviewer framing**, and **where/if the product mention lands**.

## New lessons - 2026-05-19 (09:37 CEST)
### What worked
- The strongest current market words are the plain ones already on the site: **babysitting**, **close the laptop**, **finished code by morning**, **tested code**, and **would you merge it?**
- Cross-tool and unattended-run threads are still useful when they ask for proof, review, or visible finish state instead of generic orchestration hype.
- Re-reading the actual post bodies confirms that simple comments can still work when they answer the thread directly and skip the product mention entirely.

### What did not work
- Repeating the same body logic is now a bigger risk than repeating exact phrases.
- Builder/reviewer framing is getting stale when it is used as the default middle paragraph.
- Generic orchestration/showcase threads create research signal, but they are often weak outreach targets.

### Wording and selection lessons
- Prefer site-matching phrases like **finished code**, **tested code**, **what changed**, **ready to review**, and **close the laptop** over abstract orchestration language.
- Treat **helpful reply fit** and **RalphWorkflow mention fit** as separate gates every time.
- Add a stricter body-shape check before posting: reject drafts that reuse **contrast opener -> phase split -> proof bundle -> product/link close** even if the wording is fresh.
- If the best answer is just practical workflow advice, leave RalphWorkflow out and use the thread as research only.

## New lessons - 2026-05-19 (12:21 CEST)
### What worked
- Threads about **run-until-done**, **fail-closed behavior**, **spend while away**, and **what stops the loop cleanly** are becoming stronger RalphWorkflow research than generic multi-agent threads.
- Re-reading the full logged post bodies still catches repeat-pattern risk that title-only review misses.
- The site language still works best when it stays plain: **no babysitting**, **start the job and close the laptop**, **finished code**, **tested code**, **what changed**, **would you merge it?**

### What did not work
- Search saturation keeps resurfacing already-used trust/workflow threads.
- Tactical worktree/help threads are often worth answering, but still weak places to mention RalphWorkflow.
- Builder/reviewer framing is getting stale when it becomes the default middle paragraph.

### Wording and selection lessons
- Add **bounded autonomy / fail-closed behavior** as an explicit scan lens alongside trust, review, handoff pain, and transparency.
- Keep treating **credible discussion opportunities** separately from **credible RalphWorkflow mention fits**; today the first number can still be 5-10 while the second is closer to 0-2.
- Do not count a thread as a RalphWorkflow opportunity unless it passes the **helpful reply**, **mention fit**, **prior-use**, **finish-surface**, and **body-shape freshness** gates.
- Before posting, compare against the last 3 logged bodies for **opening move**, **concept cadence**, **builder/reviewer framing**, and **where/if the product mention lands**.
- Avoid falling back to the mini-template **handoff/trust opener -> readable diff/checks line -> stale assumptions/ownership line** even when the exact wording is fresh.

## New lessons - 2026-05-19 (15:20 CEST)
### What worked
- Approval-loop, `/goal`, and **run-until-done** threads are strong current research because they expose the real question: what stops the run cleanly, what gets surfaced for review, and what still needs a human.
- Long-run production threads are useful for RalphWorkflow positioning because they keep surfacing **memory drift**, **schema drift**, and **recovery / archaeology** pain that bounded overnight runs can avoid.
- Using the live site language as the filter still keeps the message honest: **start the job and close the laptop**, **finished code**, **tested code**, **what changed**, **would you merge it?**

### What did not work
- Approval/mobile-supervision threads are increasingly crowded with product plugs, so they are often better for research than for a RalphWorkflow mention.
- Tactical cleanup/worktree threads still tempt product mentions even when the best reply is just practical git/process advice.
- Recent short comments are still converging on the same mini-shape: **handoff first -> readable diff/checks -> sketchy/stale note**.

### Wording and selection lessons
- Add **approval drag / double-confirmation** and **archaeology after long runs** as explicit scan lenses next to trust, cleanup, handoff, and fail-closed behavior.
- Treat **credible discussion opportunities** and **credible RalphWorkflow mention fits** as separate numbers every pass; today there can be 5-10 worth reading and only 0-2 worth mentioning RalphWorkflow in.
- Skip product mentions by default in threads that are mainly about **approval UX**, **mobile approvals**, or **checkpoint cleanup** unless the finish-state value case is unusually natural.
- Before posting, compare against the last 3 logged bodies for **opening move**, **mini-template cadence**, and **product-mention placement** — not just exact phrase reuse.
- Favor fresh replies built around **what stops the loop**, **what changed**, **what passed**, and **what would you merge** over builder/reviewer framing by default.

## Fresh openings — REPLACE stale handoff/diff cadence (2026-05-19)

The current short-comment shape is dead: **handoff opener → phase ownership → readable diff/checks → stale assumptions → finish state**. Every recent comment uses it. Replace with one of these per subreddit, adapted to the thread:

**Opening E — visceral failure story (good for approval/stall threads in r/ClaudeCode, r/AI_Agents):**
> "The pattern I see most is: you write a task, the agent starts, you answer a prompt, then another, it hallucinates, you correct it, and you are still there at midnight babysitting a tool that was supposed to save you time. The fix is not a better prompt — it is a clearer finish line."

**Opening F — "what did you actually ship?" challenge (good for r/programming, r/Python, r/devops):**
> "The overnight run question is usually not 'which agent should run longer' — it is 'what will I actually be able to review in the morning.' Most setups answer the first question and completely skip the second."

**Opening G — finish-line contrast, soft (good for r/codex, r/ClaudeCode):**
> "Most tooling talk focuses on the start — which model, which context window, which parallel branch. The part that actually determines whether you close the laptop is the finish: what changed, what ran, what still looks off. That is where the real workflow problem lives."

**Opening H — bounded-cost / fail-closed (good for r/entrepreneur, r/startups, r/AI_Agents):**
> "The overnight run I regret most was not the one that failed — it was the one that seemed to succeed. No visible diff, no clear receipt, just a quiet feeling that something had happened. Bounded cost with a reviewable result would have caught it."

Rule: no opening from this list may appear in more than one subreddit in the same audit window. Do not mix E/F/G/H into the old handoff/diff skeleton — these are replacements, not supplements.

## New lessons - 2026-05-19 (09:42 CEST)
### What worked
- Fresh threads about **cleanup surface**, **approval drag**, **transparency**, and **controllability** still produce the best RalphWorkflow research signal.
- Re-reading the full logged bodies still catches repeat-pattern risk that title-only review misses.
- The site language still works best when it stays plain: **no babysitting**, **start the job and close the laptop**, **finished code**, **tested code**, **what changed**, **would you merge it?**

### What did not work
- Search saturation keeps resurfacing already-used trust/workflow threads.
- Showcase/workflow-sharing posts can be worth joining, but they are often weak places to mention RalphWorkflow.
- Repetition risk is now as much about short-comment logic shape as longer body structure.

### Wording and selection lessons
- Add **transparency / controllability** as an explicit scan lens alongside trust, review, and handoff pain.
- Keep treating **credible discussion opportunities** separately from **credible RalphWorkflow mention fits**; today the first number can still be 5-10 while the second is closer to 0-2.
- Do not force a product mention into tactical setup, worktree, or cleanup threads unless the finish-state value case is unusually natural.
- Before posting, compare against the last 3 logged bodies for **opening move**, **concept cadence**, **short-comment logic shape**, and **where/if the product mention lands**.
- Avoid falling back to the mini-template **handoff/trust opener -> readable diff/checks line -> stale assumptions/ownership line** even when the exact wording is fresh.

## New lessons - 2026-05-18 (17:05 CEST)
### What worked
- Cleanup / handoff-state threads are becoming more useful than generic autonomy threads because they expose the visible-finish problem directly: what changed, what passed, what should merge, and what still needs cleanup.
- Re-reading full logged comment bodies keeps surfacing repetition that title-only review misses; the body-level check is still worth doing every pass.
- The site's simplest phrases still match the market best: **no babysitting**, **what changed**, **tests already ran**, **would you merge it?**, **start the job and close the laptop**.

### What did not work
- The live Reddit pool is now saturated enough that 5-10 shortlist-worthy threads can still collapse into only 2-3 real RalphWorkflow mention fits.
- Worktree/setup/help threads keep producing useful research signal, but many are still weak product-mention targets.
- Repetition risk is no longer just opener reuse; it is whole-body logic reuse, especially the same scope -> checks -> diff/receipt -> product-close rhythm.

### Wording and selection lessons
- Keep a three-gate shortlist: **helpful reply first**, **mention fit second**, **cleanup / handoff surface third**.
- Prefer threads where the unresolved pain is about the visible finish state: **what changed**, **what passed**, **what to merge**, **what to clean up**, or **how to re-enter safely**.
- If the thread mainly wants tactical setup or git help, keep RalphWorkflow out unless the workflow-value case is unusually natural.
- Before posting, compare the draft against the last 3 logged bodies for **opening move**, **paragraph order**, **concept cadence**, and **where/if the product mention lands**.
- Keep using plain site-matching language and avoid drifting back into abstract orchestration framing.

## New lessons - 2026-05-18 (18:15 CEST)
### What worked
- Approval-state and handoff-surface threads are still good research because they expose the real finish problem: who owns the last review step and what the human sees at the end.
- Re-reading full post bodies keeps catching a newer repetition risk that title-only review misses: the repeated **contrast opener** shape.
- Separating **credible helpful reply** from **credible RalphWorkflow mention** is still the cleanest way to stay honest in a saturated pool.

### What did not work
- The remaining unused pool leans more tactical, so many threads are worth answering but weak for product mention.
- Even fresher shorter replies can still feel repetitive when they reuse the same **X matters more than Y** opener and end with the same repo/doc slot.
- Broad multi-agent and worktree-adjacent threads still produce signal, but they are weaker than approval / finish-state threads for live RalphWorkflow positioning.

### Wording and selection lessons
- Add a fourth gate before posting: **body-shape freshness**. Reject drafts that fall back to **contrast opener -> handoff/checks -> receipt -> link**.
- Avoid defaulting to the opener family **the real problem is not X, it is Y** for the next batch of Reddit writing.
- Give extra weight to threads about **approval state**, **what changed**, **what passed**, **what to merge**, **what to clean up**, and **how to re-enter safely**.
- Keep RalphWorkflow out of tactical setup/help threads unless the workflow-value case is unusually natural and the reply would still be strong with no mention.

## New lessons - 2026-05-18 (21:15 CEST)
### What worked
- Cost / approval / governor threads reveal a useful adjacent pain: people still want unattended runs, but now they care more about **bounded failure cost** and **fail-closed behavior**.
- The strongest current unused threads are still the ones where the answer helps even with no product mention: autonomy economics, approval drag, remote supervision, and visible finish-state control.
- Re-reading full post bodies plus fresh thread scans is still the best way to catch saturation and body-shape repetition early.

### What did not work
- The best-looking current threads are increasingly vendor-pricing or migration debates; many are worth reading but weaker places to mention RalphWorkflow.
- Search saturation is worse tonight because several earlier high-fit threads are already used, leaving more tactical/helpful-only threads in the pool.
- The prior-body repetition risk is no longer just **contrast opener**; the newer repeated move is **builder/reviewer phase split + one-owner/shared-boundary + proof/link close**.

### Wording and selection lessons
- Add a **bounded-autonomy filter**: prefer threads about budgets, governors, fail-closed runs, approval drag, or morning-after review state over generic model-migration anger.
- Treat vendor-pricing rage threads as research unless the workflow answer is obviously useful without RalphWorkflow.
- Before posting, compare against the last 3 logged bodies for repeated use of **builder/reviewer split**, **one owner per shared boundary**, and the same end-slot doc/repo proof move.
- Keep using site-matching plain language: **start the job and close the laptop**, **what changed**, **would you merge it?**, **reviewable result**, **no babysitting**.

## New lessons - 2026-05-17 (15:15 CEST)
### What worked
- Threads about **what breaks first**, **review/reconstruction**, and **shared-boundary drift** are surfacing a deeper pain than generic trust threads.
- The sharpest useful language today came from the threads themselves: **one owner per shared boundary**, **merged-state check**, and **finish receipt**.
- Fresh `r/ClaudeCode` threads asking how people actually combine Claude and Codex are still the cleanest opportunities when they are explicit workflow questions.

### What did not work
- Older trust/workflow threads are now often either already used or aging out, so raw topical fit is no longer enough.
- Pure model-comparison threads still flatten into vendor preference talk unless they contain a real workflow/review pain.
- Worktree-only threads remain good market signal, but many are weaker outreach targets than threads about reconstruction, merge decisions, and morning-after review.

### Wording and selection lessons
- Add a **review/reconstruction filter** on top of freshness and prior-use gates: prefer threads asking how to understand what changed, what to merge, or what broke first.
- Mine phrases like **review/reconstruction layer**, **clean re-entry**, **one owner per shared boundary**, **merged-state check**, **finish receipt**, and **trust the finish line**.
- Keep avoiding the old **best results / reliable pattern / reviewable work units** opener family; it is still too recognizable.
- If the best answer is just tactical setup help, keep RalphWorkflow out of it.

## New lessons - 2026-05-17 (15:34 CEST)
### What worked
- Review/reconstruction threads are now stronger than generic trust threads because they expose the real morning-after pain: understanding what changed and what to merge.
- Fresh `r/ClaudeCode` posts about combining Claude and Codex still produce the clearest live opportunities when they ask an explicit workflow question.
- Site-matching plain language still holds up best: **walk away**, **reviewable result**, **proof it holds up**, **clean re-entry**.

### What did not work
- Older trust/workflow threads keep resurfacing in search and can still look attractive unless freshness and prior-use filters stay strict.
- Even fresher Reddit bodies now risk repeating the same *concept cadence* too often: small scope -> checks -> diff -> receipt -> human decision.
- Setup/help threads about worktree ergonomics keep producing signal, but many are better for research than for mentioning RalphWorkflow.

### Wording and selection lessons
- Add a **reconstruction filter**: prefer threads asking what changed, what broke first, what to merge, or how to recover cleanly after parallel work.
- Before posting, compare against the last 3 bodies for **core metaphor/cadence**, not just wording. Different words with the same diff/checks/receipt rhythm are still repetitive.
- Prefer phrases like **finish receipt**, **merged-state check**, **one owner per shared boundary**, **trust the finish line**, and **clean re-entry**.
- Use checklist-style answers only when the OP clearly asks for a checklist; otherwise avoid falling back into the same recipe shape.

## New lessons - 2026-05-17 (17:05 CEST)
### What worked
- Threads about **review/reconstruction**, **what breaks first**, and **Claude/Codex handoff structure** keep producing the strongest RalphWorkflow-fit angles.
- `r/ClaudeCode` is still the best live outreach venue; `r/codex` remains good when the thread is explicitly about trust or review, not just model preference.
- The site language still maps cleanly to the market: **too big to babysit**, **too risky to trust blindly**, **walk away and come back to something reviewable**.

### What did not work
- Repetition risk now comes from **concept cadence**, not just repeated phrases or the old opener.
- Generic comparison debates and setup-help threads still create research signal but weak outreach.
- Older high-fit threads keep polluting shortlists unless freshness and prior-use stay ahead of raw topical fit.

### Wording and selection lessons
- Add a **reconstruction novelty check** before drafting: if the answer falls back into the same small-scope -> checks -> diff -> receipt rhythm as the last 3 bodies, rewrite or skip.
- Prefer thread-native openings that answer the OP directly instead of leading with a polished thesis.
- Favor phrases like **finish receipt**, **clean re-entry**, **trust the finish line**, and **one owner per shared boundary** over older **reliable pattern / reviewable work units** wording.
- If the best contribution is just practical setup help, skip the RalphWorkflow mention entirely.

## New lessons - 2026-05-17 (18:15 CEST)
### What worked
- Fresh `r/ClaudeCode` threads about **using Claude with Codex** are currently the cleanest live opportunities because they invite direct workflow advice, not vendor tribalism.
- The strongest current pain is no longer just trust; it is **review/reconstruction after parallel work** - what changed, what broke first, and what is actually safe to merge.
- Plain site-matching language still fits best: **walk away**, **reviewable result**, **proof it holds up**, **trust the finish line**.

### What did not work
- Older high-fit trust/workflow threads still look tempting, but many are now better as research signal than as live outreach.
- Promo-adjacent builder/showcase threads often contain good ideas, but they are weaker places to mention RalphWorkflow.
- Repetition risk now includes the whole **small scope -> checks -> diff -> receipt -> human decides** cadence, even when the wording is fresh.

### Wording and selection lessons
- Add a **reconstruction filter** before shortlisting: prefer threads asking how to understand the result, what to merge, or what drifted between agents.
- Keep a strict **freshness + prior-use gate** so older trust threads do not crowd out newer live opportunities.
- When a thread is mainly about setup or tooling ergonomics, keep RalphWorkflow out unless the workflow-value case is unusually natural.
- Before drafting, compare against the last 3 bodies for **concept cadence**, not just opener/closer wording.

## New lessons - 2026-05-17 (21:15 CEST)
### What worked
- Fresh workflow-question threads about **what breaks first**, **phase handoff**, and **review/reconstruction** are still the best RalphWorkflow opportunities.
- The strongest current message is simple: the hard part is not running more agents, it is coming back to something you can reconstruct and trust.
- Same-day `r/ClaudeCode` threads are still the cleanest place to learn and occasionally reply when the question is explicitly about workflow.

### What did not work
- Older role-split threads still look attractive in search, but many are already used or too old to justify a fresh reply.
- Showcase and wrapper-demo threads can still teach us something, but they are usually weaker places to mention RalphWorkflow.
- Repetition risk now includes the broader **phase split -> checks -> diff -> receipt** cadence, not just the old opener family.

### Wording and selection lessons
- Add a **handoff-state filter**: prefer threads asking what changed, what to merge, what broke first, how to recover, or how mixed-agent permissions/state stay consistent.
- Lean into phrases like **shared-boundary drift**, **stable handoff contract**, **clean re-entry**, **finish receipt**, and **trust the finish line**.
- If a thread is mostly about setup or tooling glue, keep RalphWorkflow out unless the workflow value case is unusually natural.
- Before posting, compare against the last 3 full bodies for **opening move**, **concept cadence**, and **where/if the product mention lands**.

## New lessons - 2026-05-27 (12:45 CEST)
### What worked
- A forced-refresh pass with healthier telemetry (**49 scanned, ok=6, blocked=2**) surfaced a broader and cleaner shortlist than the earlier **03:05 CEST** snapshot, so the monitor should keep preferring fresh local reruns over stale carry-forward when coverage improves.
- Threads about **context continuity across tools** and **parallel coding agents getting messy** are still strong research fits because they expose the same visible-finish and merge-safety pain in plainer language.

### What did not work
- Better coverage still did not turn the current shortlist into strong RalphWorkflow mention targets; the honest mention-fit stayed **medium-low**.
- The `r/cursor` / `r/CursorAI` workflow-change family is now stale from a posting perspective because it was just used on **2026-05-26** and the opening line is already flagged as repetitive.

### Wording and selection lessons
- Keep **context continuity**, **parallel repo mess**, and **workflow-vs-agent boundary** as active scan lenses next to **production failure** and **visible finish state**.
- Keep the hard split between **discussion-fit** and **mention-fit** even when coverage improves; broader visibility should widen research truth before it widens posting pressure.
- Treat recent `r/cursor` workflow-change threads as prior-used/stale unless a genuinely new angle appears.

## New lessons - 2026-05-27 (21:15 CEST)
### What worked
- Another forced-refresh local rerun with still-usable telemetry (**47 scanned, ok=5, blocked=2**) held onto the broader shortlist shape instead of collapsing back to the weaker **03:05 CEST** view, which is a good sign that the monitor repair is stable.
- **Context continuity**, **parallel repo mess**, and **full-dev-environment parallelism** still surface as durable research themes even when the exact thread mix shifts slightly.

### What did not work
- The stronger shortlist still did **not** produce clean posting pressure; honest RalphWorkflow mention-fit remained **medium-low** across all four surviving threads.
- Roundup / list-style **"these tools actually work in production"** posts are useful for market language, but they still lean too promo-heavy to be comfortable RalphWorkflow mention targets.

### Wording and selection lessons
- Add **workflow-builder roundup / comparison-list posts** as a research lens, but keep them behind the same **helpful reply first / mention fit second** gate as the rest of the pool.
- Keep failing closed on posting while retrieval stays partial and the best surviving threads are still more valuable as signal than as distribution.
- When the shortlist is anchored by **context continuity** and **parallel-agent mess**, prefer extracting market language and objections over forcing another finish-state plug.

## New lessons - 2026-05-27 (22:48 CEST)
### What worked
- The degraded rerun still surfaced a real **production-failure** discussion outside the usual AI/coding communities (`r/cybersecurity`), which confirms the content-first approach is widening research truth even when coverage is thin.
- Preserving the healthier earlier same-day pass as the main market picture is more honest than letting a thinner later rerun redefine the day.

### What did not work
- Telemetry weakened sharply on the rerun (**12 scanned, ok=1, blocked=3**), so the live shortlist narrowed too much to trust as the whole market.
- The surviving threads were still better as research than as distribution; mention-fit stayed **medium-low**.

### Wording and selection lessons
- Add **security / governance reviews of agent failures** as a secondary research lens, but keep the same **helpful reply first / mention fit second** gate before treating them as posting opportunities.
- If a later forced rerun is materially thinner than an earlier same-day pass, treat it as **degraded telemetry**, not as a fresh signal that opportunities disappeared.
- Keep the hard fail-closed rule on posting whenever blocked-query count rises and the rerun breadth drops below the healthier same-day baseline.

## Structural changes — 2026-05-28 (10:30 CEST)

### Telegraph pipeline retired
- All 20 live Telegraph posts across 10+ days show 0-1 views each. The channel is dead.
- Telegraph is no longer an active distribution surface. New content goes to ralphworkflow.com/blog.
- run_posting.py should skip Telegraph and only run as a draft-hash checker. Future posting should target the Ralph-Site blog commit + deploy pipeline.

### SEO content factory created
- New agent: `agents/marketing/seo_content_factory.py`
- Covers 5 keyword gaps with live blog posts on ralphworkflow.com/blog:
  - unattended coding agent
  - AI agent orchestration CLI
  - AI coding workflow automation
  - Claude Code automation
  - spec-driven AI agent
- All 5 return 200 on the live site. Sitemap grew from 81 → 86 URLs.

### Reddit cadence — state confirmed
- Structural cadence confirmed broken across all recent posts (4-paragraph shape: contrast opener → handoff framing → proof bundle → product close)
- Posting suspended — fail-closed enforced. Monitoring continues degraded (1-3 blocked queries per pass).
- Reddit recovery path: structural rewrite package exists at `drafts/2026-05-22_reddit_structural_rewrite.md` but no autonomous execution path exists without browserless access.

### Remaining blocked lanes (2026-05-28)
- Apollo: Cloudflare auth blocked. Next review 2026-06-01.
- Dev.to: Apple OAuth blocked.
- SMTP curator email: No credentials available.
- HN/Lobsters: 7+ audit cycles stuck. Structural ceiling rule triggered.
- Comparison backlinks: 8 prepared, all status=prepared, review due 2026-06-05, no autonomous distribution path.

### New process rule
- The primary autonomous distribution lane is now ralphworkflow.com/blog content production + Codeberg/PyPI outbound linking.
- When next SEO report identifies new keyword gaps, write blog posts directly in Ralph-Site/content/blog/, commit, push, and cap deploy. Do not create draft files in drafts/ for Telegraph.
- GitHub Discussions outreach is the next autonomous lane to explore (available, unblocked, unused).
