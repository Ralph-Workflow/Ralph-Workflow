# Outreach Log

## 2026-05-18 (Monday)

### RalphWorkflow Distribution Infrastructure
- **Reddit freshness-scoring rollover fix**: Patched `agents/marketing/reddit_autopost.py` so absolute-date freshness scoring now uses the actual current date instead of the hardcoded May 17, 2026 reference; added a regression test to lock the May 18+ behavior before the next posting window opens.
  - Verification: `python3 -m unittest agents.marketing.tests.test_reddit_autopost -v`
  - Live state check: `python3 agents/marketing/reddit_watchdog.py` still correctly returns `volume_guard_active:3_posts_in_6h`, so no safe Reddit post was forced during the cooldown window.
  - Why: distribution is still the highest-leverage lane that is actually executable from this environment, and the autoposter had a date rollover bug that would increasingly mis-rank fresh vs older Reddit opportunities after May 17. Fixing that now protects the next real distribution move instead of spending another cycle on generic content.

### RalphWorkflow Distribution Infrastructure
- **Reddit watchdog retry fix**: Patched `agents/marketing/reddit_watchdog.py` so a fresh monitor report is no longer treated as permanently handled after `cooldown_skip` or `fresh_opportunity_rate_limited`; added `agents/marketing/tests/test_reddit_watchdog.py` to lock the behavior and reran the watchdog to confirm it now re-attempts the same fresh report instead of idling behind stale state.
  - Verification: `python3 -m unittest agents.marketing.tests.test_reddit_autopost agents.marketing.tests.test_reddit_watchdog -v`; `python3 agents/marketing/reddit_watchdog.py` now reaches `autopost_attempted` on `seo-reports/reddit_monitor_2026-05-17_2115.md` and reports the live burst gate instead of `already_handled`
  - Why: the strongest available move right now was fixing a real distribution stall. The watchdog had been freezing fresh Reddit opportunities after one cooldown-limited pass, which meant the marketer could miss a still-usable thread even after the rate window changed.

### RalphWorkflow Conversion
- **Hosted docs SEO + free-use landing page**: Added and pushed a new public Sphinx page, `unattended-coding-agent`, then surfaced it from the docs homepage and set `language = "en"` in Sphinx config to address the missing docs lang attribute.
  - Commit: `9836c95e` — `Add unattended coding agent landing page`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `python3 -m py_compile docs/sphinx/conf.py`; keyword/link presence checks in `docs/sphinx/index.rst` and `docs/sphinx/unattended-coding-agent.md`; local build/test unverified in this environment due to missing `uv`
  - Why: Reddit distribution was already pacing-limited, the current bottleneck is still conversion to free use, and the latest SEO audit showed zero coverage for the exact "unattended coding agent" intent plus a missing lang attribute. This turns a live search-gap term into a durable proof-led conversion page that answers what Ralph Workflow is, who it is for, why it is different, and why to try it now.

## 2026-05-17 (Sunday)

### Site messaging review — 18:15 UTC
- Reviewed live https://ralphworkflow.com against current marketing assumptions in REDDIT_LEARNINGS.md and outreach-log.md
- **Verdict:** No meaningful messaging changes. Core positioning ("unattended AI coding CLI that finishes the job"), three-phase flow, PR-review framing, and "would you merge it?" evaluation are all consistent with current marketing direction.
- Market pain signals from recent Reddit threads (review/reconstruction overhead, shared-boundary drift, "finish receipts") are covered functionally by existing site features (decision log, test suite, review bundle) but not named as marketing concepts — worth watching but no action needed now.
- **No updates required to REDDIT_LEARNINGS.md or outreach-log.md.**

### RalphWorkflow Adoption Signals
- **Public multi-agent trust-path asset**: Added and pushed a new public guide, `what-breaks-first-with-multiple-coding-agents`, across the repo docs and hosted Sphinx docs, then surfaced it from README, `START_HERE.md`, docs map, hosted docs homepage, getting-started, and quickstart.
  - Commit: `08e23231` — `Add multi-agent trust-break guide`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md docs/README.md docs/what-breaks-first-with-multiple-coding-agents.md ralph-workflow/docs/sphinx/index.rst ralph-workflow/docs/sphinx/getting-started.md ralph-workflow/docs/sphinx/quickstart.md ralph-workflow/docs/sphinx/what-breaks-first-with-multiple-coding-agents.md`; link-presence checks across all surfaced entry points; local build/test unverified in this environment due to missing `sphinx`
  - Why: Reddit posting was already pacing-limited, and the freshest live market pain is no longer just "how do I run more agents?" but "what breaks first when I come back to parallel agent work?" This turns that active pain into a durable trust/conversion asset focused on shared-boundary drift, merged-state checks, finish receipts, and clean morning-after re-entry.

### RalphWorkflow Adoption Signals
- **GitHub star/watch CTA improvement on high-intent trust paths**: Added and pushed explicit GitHub-native inspect/star/watch calls to action on the public README, `START_HERE.md`, `docs/claude-code-codex-workflow.md`, and `docs/which-agent-should-i-start-with.md` so visitors arriving from workflow/trust discussions have a cleaner path from "this fits" to a visible GitHub adoption action.
  - Commit: `228a8d95` — `Add GitHub adoption CTA on trust paths`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md docs/claude-code-codex-workflow.md docs/which-agent-should-i-start-with.md`; `grep -nE "star or watch it there|inspect Ralph Workflow on the \[GitHub mirror\]|open the mirror first" ...` across all four files
  - Why: Reddit posting was already volume-limited (`volume_guard_active:4_posts_in_6h`), conversion surfaces were broadly strong, and the clearest remaining gap was distribution-to-GitHub adoption. This tightened the inspect/star/watch path exactly where current Claude Code / Codex evaluators are most likely to land.

### RalphWorkflow Conversion
- **Public trust/adoption CTA improvement**: Surfaced repo-inspection links earlier across the hosted docs homepage hero, the main public README, and `START_HERE.md` so GitHub-native and source-first evaluators can inspect/star/watch Ralph Workflow before install instead of hunting for the repo links later in the page.
  - Commit: `2067e490` — `Surface repo inspection CTA earlier`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md ralph-workflow/docs/sphinx/index.rst`; link-presence check across all three files; local build/test unverified in this environment due to missing `sphinx`
  - Why: Reddit distribution is currently volume-limited (`volume_guard_active:5_posts_in_6h`), and GitHub adoption signals are still weak. The next best move was tightening the first-screen trust path for people already arriving on Ralph's public surfaces, especially developers who judge open-source tools by inspecting the repo before they install.

### RalphWorkflow Distribution Infrastructure
- **Reddit GitHub-link autopost upgrade**: Updated `agents/marketing/reddit_autopost.py` so the autoposter now parses `Mention fit`, identifies high-fit `r/codex` / `r/ClaudeCode` trust-workflow threads, and automatically generates a contextual GitHub mirror CTA that answers the four core marketing questions instead of leaving GitHub conversion to manual posts.
  - Verification: `python3 -m py_compile agents/marketing/reddit_autopost.py agents/marketing/tests/test_reddit_autopost.py`; `python3 -m unittest agents.marketing.tests.test_reddit_autopost -v`; functional generation check against `seo-reports/reddit_monitor_2026-05-17_1215.md` now produces a linked body for `How many of you "Trust" Codex?`
  - Why: the current bottleneck is distribution-to-GitHub conversion, and the autoposter was still shipping useful process-first replies without a reliable inspect/star/watch path. Automating the contextual GitHub mirror link is higher leverage than another one-off conversion asset because it upgrades future Reddit distribution into a repeatable GitHub-adoption surface.

### RalphWorkflow Conversion
- **Hosted docs quickstart conversion improvement**: Tightened the public Sphinx `quickstart.md` so high-intent visitors get the four-question framing, the real prerequisite that one supported agent is already installed/authenticated on their own machine, a concrete first backlog-task prompt example, and the merge-test evaluation directly on the short-path page.
  - Commit: `862632f0` — `Tighten quickstart first-run conversion`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and the quickstart path was still too mechanical for first-time evaluators. It told people how to click through setup, but not clearly enough what Ralph is, who it is for, why it is different, why to try it now, or how to judge the first run honestly. Tightening that shortest path should reduce bounce from visitors who are already ready to test Ralph tonight.
- **Hosted docs/site conversion improvement**: Strengthened the live docs homepage hero so the two highest-intent next steps are above the fold: a primary CTA to run a real first task and a secondary CTA to inspect a public example review bundle before installing.
  - Commit: `cc3daafe` — `Strengthen docs homepage proof CTA`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and the live homepage was still asking visitors to choose a generic "Get started" path before seeing the strongest trust asset. Putting the free/open-source positioning, proof-first CTA, and the "would I merge this?" evaluation directly in the hero should reduce drop-off from high-intent visitors who want either the fastest honest first run or proof before they commit time.
- **Public proof-asset publish + distribution improvement**: Published and pushed a real `example-review-bundle` proof asset into the public repo, including a sample `PROMPT.md`, morning-after result notes, review/fix handoff files, and artifact JSONs; then surfaced it from the public README, `START_HERE.md`, docs map, docs homepage, getting-started path, quickstart, first-task guide, and reviewable-output page.
  - Commit: `f239c27a` — `Publish example review bundle proof asset`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and the strongest missing trust surface was a public proof asset people could inspect before installing. Abstract claims about "reviewable output" are weaker than a real morning-after bundle high-intent visitors can open and judge with the merge test.
- **Public repo/docs trust-path improvement**: Clarified and pushed the real first-run prerequisite across the public README, `START_HERE.md`, docs map, hosted docs homepage, and hosted getting-started page: Ralph Workflow is free/open source, but you still need one supported agent CLI already installed and authenticated on your own machine before the first run.
  - Commit: `c12a1e2d` — `Clarify first-run agent prerequisites`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and the public promise was drifting a little too close to "install and go" without making the upstream agent prerequisite obvious enough. Tightening that expectation should reduce trust loss and first-run bounce from high-intent visitors who are ready to try Ralph tonight but would otherwise hit avoidable setup confusion.
- **Public proof-asset improvement**: Strengthened the public `free-open-source-proof` / hosted `reviewable-output` assets with a concrete morning-after review path, including the expected `.agent/` artifact bundle, what to open first, and the exact merge-test flow; also tightened README / START_HERE / docs-map links to point at that proof asset more explicitly.
  - Commit: `69782b30` — `Strengthen reviewable output proof asset`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and the existing proof page described reviewability in the abstract. High-intent visitors need to see the actual handoff shape — diff, `.agent/DEVELOPMENT_RESULT.md`, artifact files, and the morning-after review order — so they can picture what "reviewable output" means before deciding to try Ralph tonight.
- **Public repo/site first-run conversion improvement**: Added and pushed a copy-paste "fastest honest first run" block to the main public README, root `START_HERE.md`, and hosted docs homepage (`ralph-workflow/docs/sphinx/index.rst`).
  - Commit: `e9a9043c` — `Tighten first-run conversion path`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and even after stronger proof assets the highest-intent visitors still had to click around to reconstruct the exact first-run flow. This puts the install commands, a concrete `PROMPT.md` example, and the merge-test question directly on the primary public entry points so someone can go from curiosity to a real overnight run with less guesswork.
- **Public repo/docs conversion improvement**: Added and pushed `docs/first-task-prompt-templates.md` plus a hosted Sphinx `first-task-prompt-templates.md`, then linked the new asset from README, `START_HERE.md`, docs index, docs homepage, getting-started, quickstart, and first-task guide.
  - Commit: `8b0b38f7` — `Add first-task prompt templates`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and even a stronger start-here path still left high-intent visitors staring at a blank `PROMPT.md`. Copy-paste prompt shapes for feature work, validation, refactors, tests, and docs reduce first-run friction and make tonight's first backlog-task run more concrete.
- **Public repo onboarding improvement**: Updated `START_HERE.md` with a four-question opening, the fastest honest first-run command path, and a copy-paste `PROMPT.md` example for a real first backlog task.
  - Commit: `aea17f7a` — `Tighten start-here first-run conversion`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and the repo's public start path still asked visitors to "write a one-paragraph spec" without giving them the exact command flow and spec shape to use tonight. This makes the first run more concrete and lowers the gap between interest and actual use.

- **Hosted docs/site conversion improvement**: Added and pushed two new public Sphinx docs pages — `first-task-guide.md` and `reviewable-output.md` — then linked them from the docs homepage, getting-started flow, and quickstart.
  - Commit: `83a3fb4a` — `Add hosted docs proof assets`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the main bottleneck is still conversion to free use, and the live hosted docs path was still weaker than the repo landing path. This puts the strongest proof assets directly in the high-intent docs journey: what Ralph is, who it is for, why it is different, why use it now, how to pick a first task, and what a trustworthy reviewable handoff should look like.
- **Public repo conversion improvement**: Added and pushed a real root-level `START_HERE.md` plus a stronger README opening that explicitly answers the four marketing questions and drives visitors into the first-task / merge-test path.
  - Commit: `01d8bd8e` — `Add start-here conversion entry point`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and the live repo still lacked a true public start-here asset despite prior internal notes. This closes that gap on the public repo itself and gives high-intent visitors a sharper "what is it / who is it for / why different / why now / what should I do tonight?" path.
- **Public repo/docs improvement**: Added and pushed a new top-of-README and docs index conversion block that explicitly answers the four marketing questions and routes visitors into the right proof asset for their current objection.
  - Commit: `d7f2ac77` — `Sharpen repo conversion entry points`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: current bottleneck is still conversion to free use, so the highest-leverage move was tightening the very first repo/docs screens. Visitors now get an immediate "what is it / who is it for / why different / why now" answer plus direct paths for first run, task fit, worktrees comparison, and proof of reviewable output.
- **Public repo/docs improvement**: Added and pushed `docs/why-worktrees-are-not-enough.md` plus README / START_HERE / docs index links.
  - Commit: `e93df0dc` — `Add worktrees comparison conversion guide`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: Repeated market pain is "we already use worktrees / multiple agent sessions, so what does Ralph actually add?" A direct comparison asset is a stronger trust/conversion move than another generic post because it answers the differentiation question for high-intent users already close to trying the product.
- **Public repo/docs improvement**: Added and pushed `docs/when-unattended-coding-fits.md` plus README / START_HERE / docs index links.
  - Commit: `62cb7cb4` — `Add first-task fit guide`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: The current bottleneck is conversion to free use, so a simple good-task vs bad-task decision guide is higher leverage than another generic awareness post. It gives interested visitors a clearer first task, sharper fit test, and a faster path to an honest merge/no-merge evaluation.

## 2026-05-16 (Friday)

### RalphWorkflow Distribution
- **Reddit comment**: Posted a community-first workflow answer in `r/AI_Agents` on the thread "What's the most useful AI agent workflow you use daily?"
  - URL: https://old.reddit.com/r/AI_Agents/comments/1tcpehg/whats_the_most_useful_ai_agent_workflow_you_use/om2kp9c/
  - Status: ✅ Published
  - Notes: Used old.reddit comment form through the live local Chromium session; no link, product kept secondary, focused on spec -> isolated execute -> verify -> receipt workflow guidance.

## 2026-05-11 (Monday)

### RalphWorkflow Distribution
- **write.as article**: "Spec-Driven Development: How to Run Claude Code Unattended" — https://write.as/310x20gr2ozpg.md
  - Status: ✅ Published
  - Clean version without external links (write.as blocked product URLs)
- **Dev.to**: ❌ Failed — email/password login says "account not confirmed"; GitHub OAuth requires GitHub credentials not available
- **r/programming**: Reddit network-blocked from this host
- **Hacker News**: ✅ Accessible — no active account; need account to submit
- **Lobsters**: Not checked (requires account)

### HireAegis Distribution
- No posts today

### Platform Status Summary
| Platform | Status |
|----------|--------|
| write.as | ✅ Working (anonymous) |
| Dev.to | ❌ Auth blocked (email unconfirmed, no GitHub creds) |
| Reddit | ❌ Network-blocked |
| Hacker News | ⚠️ Accessible but requires account |
| Lobsters | ⚠️ Needs account |
| LinkedIn | Not checked |

---

## 2026-05-10 (Sunday)

### RalphWorkflow Distribution
- **Dev.to article**: Adapted from YouTube script — "The Real Problem with AI Coding Tools in 2026 — It's Not the Tools" (DRAFTED, not yet published)
- **r/programming**: Not yet posted
- **r/typescript**: Not yet posted
- **Hacker News**: Not yet submitted
- **Lobsters**: Not yet posted

### HireAegis Distribution
- **LinkedIn**: Not yet posted
- **r/recruiting**: Not yet posted

---

_Last updated: 2026-05-11 06:29 UTC_

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-16_0549.md`
- **Scan summary:** 31 candidate Reddit threads scanned, 8 shortlisted, 23 rejected.
- **Current verdict:** No strong RalphWorkflow mention opportunity right now.
- **Best discussion fits if a helpful comment is warranted later:**
  - `r/ClaudeCode` — "Claude Code + Codex Workflow?"
  - `r/ClaudeAI` — "Running two Claude Code agents on the same repo simultaneously. Git worktrees make it work."
- **Repeated pains worth tracking:** approval stalls while away from desk, manual Claude/Codex glue work, worktree setup friction, need for reviewable checkpoints instead of blind trust.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1t1g6fv/are_you_all_still_managing_multiple_agent/
- **Comment URL:** https://old.reddit.com/r/ClaudeCode/comments/1t1g6fv/are_you_all_still_managing_multiple_agent/om2to0p/
- **Status:** ✅ Published
- **Notes:** Autoposted from reddit-monitor shortlist: #1 Are you all still managing multiple agent sessions manually? (r/ClaudeCode).

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-16_0917.md`
- **Scan summary:** 34 candidate Reddit threads/posts scanned, 7 shortlisted, 27 rejected.
- **Current verdict:** ✅ Strong opportunity found.
- **Best current discussion fits:**
  - `r/ClaudeCode` — "Claude code agents going off the rails overnight: what's biting you?"
  - `r/ClaudeCode` — "Claude Code + Codex Workflow?"
  - `r/codex` — "How many of you "Trust" Codex?"
- **Repeated pains worth tracking:** overnight drift, weak verification before "done", approval drag, review-in-big-batches, worktrees without scope control.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1t9fl7h/claude_code_agents_going_off_the_rails_overnight/
- **Comment URL:** https://old.reddit.com/r/ClaudeCode/comments/1t9fl7h/claude_code_agents_going_off_the_rails_overnight/om3bpdb/
- **Status:** ✅ Published
- **Notes:** Autoposted from reddit-monitor shortlist: #1 Claude code agents going off the rails overnight: what's biting you? (r/ClaudeCode).

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-16_1415.md`
- **Scan summary:** 31 candidate Reddit threads/posts scanned, 8 shortlisted, 23 rejected.
- **Current verdict:** ✅ Strong opportunity found.
- **Best current discussion fits:**
  - `r/ClaudeCode` — "Critique my Workflow"
  - `r/ClaudeCode` — "I let Claude Code on web run overnight and it actually shipped something useful"
  - `r/codex` — "Anyone else using Claude Code + Codex together?"
- **Repeated pains worth tracking:** manual Claude/Codex glue, need for workflow critique, reviewable overnight results, trust via checks not branding, worktrees without enough finish discipline.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1u0g0cu/critique_my_workflow/
- **Status:** ❌ Failed
- **Notes:** Ran `reddit_autopost.py` against the latest shortlist. It selected `r/ClaudeCode` — "Critique my Workflow" but Playwright timed out waiting for the old Reddit comment textarea, so no comment was published.

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-16_2008.md`
- **Scan summary:** 29 candidate Reddit threads/posts scanned, 8 shortlisted, 21 rejected.
- **Current verdict:** ✅ 8 credible opportunities found; strongest fit remains workflow-first replies with optional/no product mention.
- **Best current discussion fits:**
  - `r/ClaudeCode` — "Critique my Workflow"
  - `r/ClaudeCode` — "How are you handling merge safety when running multiple coding agents on the same repo?"
  - `r/codex` — "Use claude code with codex?"
- **Repeated pains worth tracking:** semantic conflicts after clean merges, approval/draft-state friction, worktree env/handoff friction, need for an independent final review instead of trusting agent self-reports.
- **Posting note:** No posting attempted from this monitor pass.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1tdvnnt/critique_my_workflow/
- **Comment URL:** https://old.reddit.com/r/ClaudeCode/comments/1tdvnnt/critique_my_workflow/om67t5g/
- **Status:** ✅ Published
- **Notes:** Autoposted from reddit-monitor shortlist: #1 Critique my Workflow (`r/ClaudeCode`).

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-16_2218.md`
- **Scan summary:** 29 candidate Reddit threads/posts scanned, 8 shortlisted, 21 rejected.
- **Current verdict:** ✅ 8 credible opportunities still available; shortlist remained stable versus the 19:15 and 20:08 CEST scans.
- **Best current discussion fits:**
  - `r/ClaudeCode` — "Critique my Workflow"
  - `r/ClaudeCode` — "How are you handling merge safety when running multiple coding agents on the same repo?"
  - `r/codex` — "Use claude code with codex?"
- **Repeated pains worth tracking:** merge-safety beyond worktrees, draft-state/approval-loop friction, independent final review, manual Claude/Codex glue, and clean re-entry after unattended runs.
- **Posting note:** No posting attempted from this monitor pass.

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-16_2215.md`
- **Scan summary:** 30 candidate Reddit threads/posts scanned, 6 shortlisted, 24 rejected.
- **Current verdict:** ✅ 6 credible opportunities found; strongest late-night fits are direct workflow-question threads, not showcase posts.
- **Best current discussion fits:**
  - `r/ClaudeCode` — "Claude Code + Codex Workflow?"
  - `r/ClaudeCode` — "Worktrees in Claude Code Desktop App"
  - `r/ClaudeCode` — "Run both Claude code and codex"
- **Repeated pains worth tracking:** approval/draft-state review loops, Claude/Codex handoff glue work, worktree env/bootstrap friction, need for independent review before trusting unattended output.
- **Posting note:** No posting attempted from this monitor pass.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/codex/comments/1tath73/use_claude_code_with_codex/
- **Comment URL:** https://old.reddit.com/r/codex/comments/1tath73/use_claude_code_with_codex/om6v981/
- **Status:** ✅ Published
- **Notes:** Autoposted from reddit-monitor shortlist: #3 Use claude code with codex? (`r/codex`).
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### Marketing momentum watchdog
- **When:** 2026-05-16 22:26:56
- **Note:** Momentum check found: no_recent_reddit_post. Keep research/posting pressure on RalphWorkflow.

### RalphWorkflow Distribution
- **write.as article**: "Claude Code + Codex Workflow: Plan, Build, Review" — https://write.as/vesqh0lzrm4en.md
  - Status: ✅ Published
  - Notes: Owned-content asset published as the primary non-Reddit marketing piece; positioned around reviewable output, proof over claims, and unattended runs that actually hold up.

### RalphWorkflow Distribution
- **write.as article**: "How to Tell if an AI Coding Task Is Actually Done" — https://write.as/7pqpd2y0v0re2.md
  - Status: ✅ Published
  - Notes: Second owned-content asset published; focused on proof of completion, reviewability, and the merge test as the real definition of done.

### Owned-content drafting
- Drafted site-guide assets:
  - `drafts/2026-05-16_unattended-ai-coding-when-it-works_site_guide.md`
  - `drafts/2026-05-16_reviewable-ai-code-output_site_guide.md`
- Purpose: convert current market pain into durable owned pages that can drive adoption beyond Reddit.

### RalphWorkflow Distribution
- **write.as article**: "When Unattended AI Coding Actually Works" — https://write.as/x5wil6pmtbvo1.md
  - Status: ✅ Published
  - Notes: Owned-content asset focused on when unattended coding is genuinely useful, how to keep it reviewable, and where RalphWorkflow fits.

### RalphWorkflow Distribution
- **write.as article**: "Reviewable AI Code Output" — https://write.as/lsgmbq5ok5cj7.md
  - Status: ✅ Published
  - Notes: Owned-content asset focused on the difference between raw AI output and a result a human can actually review and merge.

### RalphWorkflow Distribution
- **write.as article**: "Start Here: Try Ralph Workflow on One Real Backlog Task" — https://write.as/9fd522xefd86z.md
  - Status: ✅ Published
  - Notes: Conversion-focused owned asset aimed at turning interest into an actual first trial and a merge/no-merge decision.

### Conversion / adoption assets
- Added root `README.md` for RalphWorkflow with clearer adoption-first framing.
- Added `START_HERE_RALPHWORKFLOW.md` to turn interest into a first real trial.
- Added proof assets:
  - `content/examples/first_task_example.md`
  - `content/examples/review_bundle_example.md`
- Purpose: improve conversion from repo/site curiosity into actual trial and adoption.

### Adoption conversion work
- Drafted `drafts/2026-05-16_why-worktrees-are-not-enough_site_guide.md`
- Drafted `drafts/2026-05-16_trial-cta-snippets.md`
- Purpose: tighten proof/positioning and improve trial conversion instead of just adding more generic awareness content.

### Public repo adoption improvements
- Shipped conversion improvements to the public RalphWorkflow repo.
- Commit: `6ca12e02` — `Improve trial conversion docs`
- Added public `START_HERE.md`
- Added public `docs/trial-proof.md`
- Updated root `README.md` and `docs/README.md` to push visitors toward a real first trial and a merge/no-merge evaluation.

### Free and open-source framing cleanup
- Replaced remaining RalphWorkflow marketing/public wording that used "trial" framing.
- Public repo commit: `89345ba7` — `Replace trial wording with free OSS framing`
- Renamed public doc `docs/trial-proof.md` -> `docs/free-open-source-proof.md`
- Updated public README/docs references and internal marketing materials to use free/open-source adoption wording instead.

### RalphWorkflow Conversion
- **Public docs/site conversion improvement**: Updated the Sphinx docs homepage and getting-started page to answer the four marketing questions earlier, push the right first-task framing, and anchor evaluation on the merge test.
  - Commit: `55967acb` — `Sharpen docs conversion messaging`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: current bottleneck is conversion to free use, and docs visitors were landing on a mostly technical path before seeing the strongest fit / trust / why-now framing. This makes the docs surface act more like a serious adoption entry point instead of just reference material.

### Marketing momentum watchdog
- **When:** 2026-05-17 04:35:07
- **Note:** Momentum check found: reddit_monitor_stale. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 05:05:06
- **Note:** Momentum check found: reddit_monitor_stale. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 05:35:07
- **Note:** Momentum check found: reddit_monitor_stale. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 06:05:07
- **Note:** Momentum check found: reddit_monitor_stale. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 06:35:07
- **Note:** Momentum check found: reddit_monitor_stale, no_recent_reddit_post. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 07:05:06
- **Note:** Momentum check found: reddit_monitor_stale, no_recent_reddit_post. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 07:35:07
- **Note:** Momentum check found: reddit_monitor_stale, no_recent_reddit_post. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 08:05:07
- **Note:** Momentum check found: reddit_monitor_stale, no_recent_reddit_post. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 08:36:09
- **Note:** Momentum check found: reddit_monitor_stale, no_recent_reddit_post. Keep research/posting pressure on RalphWorkflow.

### Marketing momentum watchdog
- **When:** 2026-05-17 09:05:07
- **Note:** Momentum check found: reddit_monitor_stale. Keep research/posting pressure on RalphWorkflow.

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-17_0915.md`
- **Scan summary:** 28 candidate Reddit threads/posts scanned, 6 shortlisted, 22 rejected.
- **Current verdict:** ✅ 6 credible opportunities found; strongest fits are merge-safety, workflow-critique, and Claude/Codex handoff threads.
- **Best current discussion fits:**
  - `r/ClaudeCode` — "Critique my Workflow"
  - `r/ClaudeCode` — "How are you handling merge safety when running multiple coding agents on the same repo?"
  - `r/ClaudeCode` — "Claude Code + Codex Workflow?"
- **Repeated pains worth tracking:** merge-safe finish beyond worktrees, approval/draft-state friction, manual Claude/Codex review glue, weak overnight stop conditions, and need for a clean morning-after re-entry point.
- **Posting note:** No posting attempted from this monitor pass.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1t9prcp/how_are_you_handling_merge_safety_when_running/
- **Comment URL:** https://old.reddit.com/r/ClaudeCode/comments/1t9prcp/how_are_you_handling_merge_safety_when_running/om9n4uw/
- **Status:** ✅ Published
- **Notes:** Autoposted from reddit-monitor shortlist: #2 How are you handling merge safety when running multiple coding agents on the same repo? (`r/ClaudeCode`).
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### RalphWorkflow Conversion
- **Hosted docs trust/fit-path improvement**: Added and pushed a dedicated Sphinx `when-unattended-coding-fits.md` page, then surfaced it directly on the hosted docs homepage with a new good-fit/bad-fit section, a prominent fit-check card, and homepage copy that routes uncertain visitors into the fit filter before they waste a first run.
  - Commit: `d088768f` — `Surface task-fit guidance on docs homepage`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and the hosted docs homepage was strong on setup and proof but still weak at qualifying whether a visitor's first task was a good unattended fit. Making the fit filter visible earlier should improve trust, reduce bad first-run experiences, and help high-intent evaluators reach a cleaner merge/no-merge test faster.
- **Public repo/docs adoption-signal improvement**: Surfaced the synced GitHub mirror on the main public README, `START_HERE.md`, hosted docs homepage, and hosted getting-started page so visitors who evaluate open-source tools on GitHub can inspect, star, or watch Ralph Workflow without hunting for the mirror.
  - Commit: `8985452f` — `Surface GitHub mirror on adoption entry points`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, but public adoption signals are also weak — especially on GitHub. The public entry surfaces were still effectively Codeberg-only even though a healthy synced GitHub mirror exists. Making that mirror explicit should reduce trust friction for GitHub-native evaluators and create an easier path to stars/watches from people who already like the free/open-source positioning and proof assets.
- **Public repo/docs differentiation improvement**: Added and pushed a public `Ralph Workflow vs Aider` comparison page into both the repo docs and hosted Sphinx docs, then surfaced it from the main README, `START_HERE.md`, docs map, docs homepage, getting-started flow, and quickstart.
  - Commit: `cd50c00d` — `Add Aider comparison trust path`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Why: the current bottleneck is still conversion to free use, and high-intent evaluators who already like interactive AI coding tools need a crisp answer to a common trust question: "why use Ralph instead of Aider?" A direct comparison asset tightens differentiation on a real competitor frame, answers the four marketing questions in that context, and gives repo/docs visitors a cleaner path from curiosity to an honest first overnight run.

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-17_1215.md`
- **Scan summary:** 31 candidate Reddit threads/posts scanned, 6 shortlisted, 25 rejected.
- **Current verdict:** ✅ 6 credible opportunities found, but freshness is weaker than the best May 16 / May 17 morning passes because some high-fit threads are now older or already used.
- **Best current discussion fits:**
  - `r/ClaudeCode` — "Run both Claude code and codex"
  - `r/ClaudeCode` — "Do you actually read and review the code generated by AI Agent or just trust whatever the AI Agent give to you?"
  - `r/codex` — "How many of you "Trust" Codex?"
- **Repeated pains worth tracking:** approval/draft-state friction, trust without blind faith, Claude/Codex handoff glue, merge-safe finish beyond worktrees, and the need for a clean morning-after re-entry point.
- **Risk note:** prior RalphWorkflow Reddit bodies are now repeating the same full structure too often; future drafts need a freshness gate, prior-use gate, and body-shape variation check.
- **Posting note:** No posting attempted from this monitor pass.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1t70rwk/run_both_claude_code_and_codex/
- **Comment URL:** https://old.reddit.com/r/ClaudeCode/comments/1t70rwk/run_both_claude_code_and_codex/oma6hnn/
- **Status:** ✅ Published
- **Notes:** Fresh body — no thesis opener, no soft last-paragraph Ralph mention. Direct role-split advice from a different angle.
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1t7fi55/do_you_actually_read_and_review_the_code/
- **Comment URL:** https://old.reddit.com/r/ClaudeCode/comments/1t7fi55/do_you_actually_read_and_review_the_code/oma6in3/
- **Status:** ✅ Published
- **Notes:** Fresh body — no thesis opener, no soft last-paragraph Ralph mention. Review-first angle targeting skeptical audience.
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

## 2026-05-17 (Sunday) — Evening Audit — 21:20 UTC
- **Bottleneck verdict:** distribution-to-free-use conversion, not surface quality. GitHub stars: 0. Codeberg stars: 9.
- **What's working:** Reddit distribution with fresh bodies, pacing guards, varied shapes. Conversion surfaces genuinely strong.
- **What's broken:** 6 write.as articles (May 11-16) have zero distribution — never submitted to HN, Lobsters, or any real-reach platform. Dead weight.
- **Repetition risk:** repeated opening line still alive in autoposter; body-template fix needs end-to-end verification, not just compile check.
- **What to stop:** adding conversion assets. Diminishing returns. Surfaces are strong enough.
- **Next highest-leverage move:** (1) Submit strongest write.as articles to HN and Lobsters with GitHub mirror links. (2) Verify autoposter auto-injects GitHub CTA in every high-fit Reddit body end-to-end.
- **Rules triggered:** four marketing questions still answered in all assets. Free OSS framing preserved. Bottleneck shift confirmed.

---

## 2026-05-17 (Sunday) — Fresh Reddit Posts (Audit Response)

### RalphWorkflow Distribution
- **Reddit comment**: "Run both Claude code and codex" (r/ClaudeCode, fresh Saturday thread)
  - URL: https://old.reddit.com/r/ClaudeCode/comments/1t70rwk/run_both_claude_code_and_codex/oma6hnn/
  - Status: ✅ Published
  - Notes: Fresh body — no thesis opener, no soft last-paragraph Ralph mention. Direct role-split advice: one implements, one reviews/challenges, judge on diff+checks.

- **Reddit comment**: "Do you actually read and review the code or just trust whatever the AI Agent gives you?" (r/ClaudeCode)
  - URL: https://old.reddit.com/r/ClaudeCode/comments/1t7fi55/do_you_actually_read_and_review_the_code/oma6in3/
  - Status: ✅ Published
  - Notes: Fresh body — no thesis opener, no soft last-paragraph Ralph mention. Review-first angle targeting the skeptical audience: trust the diff, not the agent's confidence.

### Workflow direction update — 2026-05-17 12:25 UTC
- **Decision**: Distribution is now the bottleneck, not conversion surfaces.
- **Conversion assets are strong enough**: proof bundle, first-task templates, START_HERE, quickstart, task-fit guide, Aider comparison, homepage — all ship and surface correctly.
- **Next moves**: (1) Keep Reddit posting pressure up with fresh body shapes, (2) add a freshness gate to reddit_autopost.py to prevent reusing the same hardcoded body templates, (3) get write.as articles distributed beyond their own URLs, (4) consider Reddit posts that link to GitHub mirror to move GitHub stars.
- **What to stop doing**: adding more conversion assets while distribution is thin and body repetition is actively degrading post quality.

### RalphWorkflow Distribution Infrastructure
- **Reddit autopost freshness/variation fix**: Updated `agents/marketing/reddit_autopost.py` so it now parses thread freshness from the monitor report, prefers fresher unused threads, scores out older/staler opportunities, and generates comment bodies from multiple thread-type variants instead of one hardcoded shape.
  - Verification: `python3 -m py_compile agents/marketing/reddit_autopost.py` plus a functional dry-run import check against `seo-reports/reddit_monitor_2026-05-17_1215.md`
  - Result: the autoposter now selects the fresh `Run both Claude code and codex` opportunity and produces a non-repetitive body with `body_needs_regeneration: false`
  - Why: distribution is the bottleneck right now, and repetitive Reddit bodies were degrading quality. Fixing the autopost generator is higher leverage than adding another conversion asset because it improves every future distribution pass.
- **Reddit autopost stale-thread guardrail**: Tightened `agents/marketing/reddit_autopost.py` and `reddit_watchdog.py` so the system now refuses to fall through to stale leftover shortlist entries after the genuinely fresh threads from a report have already been used, and it records terminal skip states instead of repeatedly nudging the same aging report.
  - Verification: `python3 -m py_compile agents/marketing/reddit_autopost.py agents/marketing/reddit_watchdog.py`; probe against `seo-reports/reddit_monitor_2026-05-17_1215.md` now scores freshness correctly (`Moving from claude code to codex` / `People running 2–5 coding agents...` = stale) and returns `fresh_rate_limited` instead of selecting an old thread.
  - Why: distribution is the current bottleneck, but low-quality stale Reddit replies are worse than waiting for the next genuinely fresh opportunity. This protects account quality and keeps the marketer from burning distribution energy on aging threads just because the report still has unused rows.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/codex/comments/1t5uwtc/how_many_of_you_trust_codex/
- **Comment URL:** https://old.reddit.com/r/codex/comments/1t5uwtc/how_many_of_you_trust_codex/omaao0k/
- **Status:** ✅ Published
- **Notes:** Manual post from reddit-monitor shortlist: trust workflow answer for r/codex.
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### RalphWorkflow Distribution
- **Reddit comment**: "How many of you \"Trust\" Codex?" (`r/codex`)
  - URL: https://old.reddit.com/r/codex/comments/1t5uwtc/how_many_of_you_trust_codex/omaao0k/
  - Status: ✅ Published
  - Why: distribution is the current bottleneck, and this was the strongest still-unused trust thread in the latest shortlist. The reply keeps Ralph secondary, answers the blind-trust pain directly, and reinforces the core promise: walk away and come back to a reviewable result instead of a transcript that only sounds done.
- **Reddit distribution infrastructure improvement**: Added and verified pacing guards in `agents/marketing/reddit_autopost.py` / `reddit_watchdog.py` so the autoposter now skips when the account has posted too recently, caps burst volume in the last 6 hours, and prefers communities that have not just been hit.
  - Verification: `python3 -m py_compile agents/marketing/reddit_autopost.py agents/marketing/reddit_watchdog.py`; live autopost check returned `cooldown_skip` with `global_cooldown_active:30m_since_last_post`; watchdog now treats that as already handled.
  - Why: distribution is the current bottleneck, but posting several Reddit comments in a tight window is a quality/account-risk pattern. Throttling the autoposter is higher leverage than forcing another weak or spammy reply because it protects future distribution quality across every run.
- **Reddit distribution infrastructure improvement**: Tightened `agents/marketing/reddit_autopost.py` so the live body generator now distinguishes Claude→Codex relay threads, mixed-agent team threads, and "what breaks first" trust threads instead of collapsing them into the same Codex/workflow reply shape; high-fit `r/ClaudeCode` bodies keep the GitHub mirror CTA while rotating the underlying argument and handoff angle.
  - Verification: `python3 -m py_compile agents/marketing/reddit_autopost.py agents/marketing/tests/test_reddit_autopost.py`; `python3 -m unittest agents.marketing.tests.test_reddit_autopost -v`; functional generation probe against `seo-reports/reddit_monitor_2026-05-17_2115.md` now yields distinct GitHub-linked bodies for `Claude -> Codex -> Claude` and `Claude Code Agent Teams W/ Gemini and Codex`, plus a separate merge/re-entry shape for `People running 2–5 coding agents: what actually breaks first for you?`
  - Why: the account is currently inside the Reddit pacing window (`volume_guard_active:5_posts_in_6h`), so the highest-leverage action available right now was to improve the next safe post rather than force another comment. This fixes a real distribution-quality leak: different high-fit threads were still converging on nearly identical bodies, which risks looking canned right where GitHub-conversion links matter most.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/codex/comments/1tf4l2i/codex_feels_like_a_vibe_coders_dream_after_months/
- **Comment URL:** https://old.reddit.com/r/codex/comments/1tf4l2i/codex_feels_like_a_vibe_coders_dream_after_months/omanifm/
- **Status:** ✅ Published
- **Notes:** Manual post on a fresh r/codex workflow/trust thread with a contextual GitHub mirror link.
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### RalphWorkflow Distribution
- **Reddit comment**: Posted on fresh `r/codex` thread "Codex Feels Like a Vibe Coders Dream After Months of Fighting Claude"
  - URL: https://old.reddit.com/r/codex/comments/1tf4l2i/codex_feels_like_a_vibe_coders_dream_after_months/omanifm/
  - Status: ✅ Published
  - Why: distribution-to-GitHub conversion is the current bottleneck, and this was a same-day high-attention workflow/trust thread where a GitHub mirror link fit naturally. The reply stayed process-first, answered the trust pain, and gave interested readers a direct path to inspect/star/watch the free open-source project on GitHub.

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-17_1515.md`
- **Scan summary:** 29 candidate Reddit threads/posts scanned, 7 shortlisted, 22 rejected.
- **Current verdict:** ✅ 7 credible opportunities found, but only the top few are still strong live-outreach targets after freshness and prior-use filtering.
- **Best current unused discussion fits:**
  - `r/ClaudeCode` — "Using Claude with Codex, anyone else?"
  - `r/ClaudeCode` — "People running 2–5 coding agents: what actually breaks first for you?"
  - `r/ClaudeCode` — "Claude -> Codex -> Claude"
- **Repeated pains worth tracking:** review/reconstruction overhead, config/schema/shared-boundary drift, merged-state checks, finish receipts, and clean morning-after re-entry.
- **Risk note:** many older trust/workflow threads are now either already used or aging out, so freshness + prior-use must outrank raw topical fit.
- **Posting note:** No posting attempted from this monitor pass.

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-17_1534.md`
- **Scan summary:** 30 candidate Reddit threads/posts scanned, 7 shortlisted, 23 rejected.
- **Current verdict:** ✅ 7 credible opportunities found today; top live fits are still workflow-question threads, not showcase posts.
- **Best current unused discussion fits:**
  - `r/ClaudeCode` — "Using Claude with Codex, anyone else?"
  - `r/ClaudeCode` — "People running 2–5 coding agents: what actually breaks first for you?"
  - `r/ClaudeCode` — "Claude -> Codex -> Claude"
- **Repeated pains worth tracking:** review/reconstruction overhead, shared-boundary drift, finish receipts, merged-state confidence, and clean morning-after re-entry.
- **Risk note:** prior post risk is now about repeated *concept cadence* as well as wording; even fresh phrasing can feel canned if it replays the same diff/checks/receipt structure.
- **Posting note:** No posting attempted from this monitor pass.

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-17_1815.md`
- **Scan summary:** 30 candidate Reddit threads/posts scanned, 7 shortlisted, 23 rejected.
- **Current verdict:** ✅ 7 credible opportunities found; only the top 3-4 look like strong live-outreach targets right now.
- **Best current unused discussion fits:**
  - `r/ClaudeCode` — "Using Claude with Codex, anyone else?"
  - `r/ClaudeCode` — "People running 2–5 coding agents: what actually breaks first for you?"
  - `r/ClaudeCode` — "Claude -> Codex -> Claude"
- **Repeated pains worth tracking:** review/reconstruction overhead, shared-boundary drift, finish receipts, manual Claude/Codex glue, and weak stop conditions on overnight runs.
- **Risk note:** repeat-pattern risk is now the full **small scope -> checks -> diff -> receipt -> human decides** cadence, not just reused phrases.
- **Posting note:** No posting attempted from this monitor pass.

### Reddit monitoring
- **Report:** `seo-reports/reddit_monitor_2026-05-17_2115.md`
- **Scan summary:** 30 candidate Reddit threads/posts scanned, 7 shortlisted, 23 rejected.
- **Current verdict:** ✅ 7 credible opportunities found; strongest live fits are still workflow-question threads, especially `r/ClaudeCode` discussions about what breaks first, Claude→Codex→Claude handoff, and mixed-agent team handoff state.
- **Best current unused discussion fits:**
  - `r/ClaudeCode` — "People running 2–5 coding agents: what actually breaks first for you?"
  - `r/ClaudeCode` — "Claude -> Codex -> Claude"
  - `r/ClaudeCode` — "Claude Code Agent Teams W/ Gemini and Codex"
- **Repeated pains worth tracking:** review/reconstruction overhead, shared-boundary drift, finish receipts, manual Claude/Codex glue, overnight drift, and mixed-agent permission/session-state mismatch.
- **Risk note:** repeat-pattern risk now includes the broader **phase split -> checks -> diff -> receipt** cadence plus the familiar **"that’s why I built RalphWorkflow"** product-mention slot.
- **Posting note:** No posting attempted from this monitor pass.
- **Public repo/docs workflow-fit improvement**: Added and pushed a new public `Claude Code + Codex workflow` guide across the repo docs and hosted Sphinx docs, then surfaced it from the main README, `START_HERE.md`, docs map, docs homepage, getting-started path, and quickstart.
  - Commit: `430a3c14` — `Add Claude Code + Codex workflow trust path`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md docs/README.md docs/claude-code-codex-workflow.md ralph-workflow/docs/sphinx/index.rst ralph-workflow/docs/sphinx/getting-started.md ralph-workflow/docs/sphinx/quickstart.md ralph-workflow/docs/sphinx/claude-code-codex-workflow.md`; link-presence check across README / START_HERE / docs map / docs homepage / getting-started / quickstart; local build/test unverified in this environment due to missing `sphinx`
  - Why: Reddit posting was temporarily volume-limited (`volume_guard_active:4_posts_in_6h`), and the freshest live demand is still Claude Code + Codex workflow pain. Turning that active distribution angle into a durable public trust asset gives future Reddit / SEO / GitHub visitors a specific answer to a real workflow question instead of another generic onboarding page.
- **Public repo/site agent-path conversion improvement**: Surfaced the highest-intent agent-specific first-run paths earlier on the hosted docs homepage hero follow-up, the public README start block, and `START_HERE.md`, pointing Claude Code / Codex visitors straight to `Which Agent Should I Start With?`, `Claude Code + Codex Workflow`, and the public review bundle.
  - Commit: `8ff13f97` — `Surface agent-path CTA on adoption entry points`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md docs/sphinx/index.rst`; link-presence check for `which-agent-should-i-start-with`, `claude-code-codex-workflow`, and `example-review-bundle` across all three files; local build/test unverified in this environment due to missing `sphinx`
  - Why: Reddit distribution is temporarily volume-limited (`volume_guard_active:4_posts_in_6h`), and current inbound demand is heavily Claude Code / Codex workflow-shaped. Surfacing the matching paths earlier should reduce bounce from high-intent visitors who already know their agent setup and just need the fastest trustworthy way to try Ralph tonight.
- **Public repo/docs merge-trust improvement**: Added and pushed a new public `How to Review AI Coding Output Before You Merge` guide across the repo docs and hosted Sphinx docs, then surfaced it from the main README, `START_HERE.md`, docs map, docs homepage, getting-started path, and quickstart.
  - Commit: `9f9c7981` — `Add merge-review trust path`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md docs/README.md docs/review-ai-coding-output-before-merge.md ralph-workflow/docs/sphinx/index.rst ralph-workflow/docs/sphinx/getting-started.md ralph-workflow/docs/sphinx/quickstart.md ralph-workflow/docs/sphinx/review-ai-coding-output-before-merge.md`; link-presence check for `review-ai-coding-output-before-merge` across all surfaced entry points; local build/test unverified in this environment due to missing `sphinx`
  - Why: the latest Reddit/market pain is shifting from "can I run more agents?" toward "how do I know the morning-after result is actually safe to merge?" With Reddit pacing still active, the highest-leverage move was a durable public trust asset that gives high-intent evaluators a concrete five-minute merge check: diff, finish receipt, real checks, shared-boundary review, then the merge question.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1tf720n/using_claude_with_codex_anyone_else/
- **Comment URL:** https://old.reddit.com/r/ClaudeCode/comments/1tf720n/using_claude_with_codex_anyone_else/ombxdri/
- **Status:** ✅ Published
- **Notes:** Manual post after autopost parser fix: Using Claude with Codex, anyone else? (r/ClaudeCode).
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`
- **Public repo/docs Claude Code trust-path improvement**: Added and pushed a new public `Ralph Workflow vs Claude Code` comparison page across the repo docs and hosted Sphinx docs, then surfaced it from the main README, `START_HERE.md`, docs map, package README, hosted docs homepage, getting-started path, and quickstart.
  - Commit: `55ecf57a` — `Add Claude Code comparison trust path`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md docs/README.md docs/ralph-workflow-vs-claude-code.md ralph-workflow/README.md ralph-workflow/docs/sphinx/index.rst ralph-workflow/docs/sphinx/getting-started.md ralph-workflow/docs/sphinx/quickstart.md ralph-workflow/docs/sphinx/ralph-workflow-vs-claude-code.md`; link-presence check for `ralph-workflow-vs-claude-code` across all surfaced entry points; local build/test unverified in this environment due to missing `sphinx`
  - Why: live demand is heavily Claude Code-shaped, and high-intent evaluators still need a crisp answer to the question "why add Ralph instead of just staying in Claude Code?" A direct comparison asset is a stronger conversion move than another generic article because it answers the fit, differentiation, and why-now questions exactly where current workflow interest already exists.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/codex/comments/1tao42q/did_anyone_here_moved_from_claude_to_codex/
- **Comment URL:** https://old.reddit.com/r/codex/comments/1tao42q/did_anyone_here_moved_from_claude_to_codex/omc9mon/
- **Status:** ✅ Published
- **Notes:** Manual post on fresh r/codex migration thread: Claude-to-Codex workflow answer with reviewable-handoff angle.
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### RalphWorkflow Distribution
- **Reddit comment**: Posted on fresh `r/codex` thread "Did anyone here moved from claude to codex recently? And why?"
  - URL: https://old.reddit.com/r/codex/comments/1tao42q/did-anyone-here-moved-from-claude-to-codex-recently-and-why/omc9mon/
  - Status: ✅ Published
  - Why: live demand is still strongest around Claude-vs-Codex workflow choice, and this same-day migration thread had active discussion plus room for a process-first answer. The reply shifted the frame from model fandom to phase ownership and reviewable handoff, then gave interested readers a direct GitHub path to inspect the free open-source project.

### RalphWorkflow Conversion

- **Homepage GitHub-inspection CTA improvement**: Added and pushed a new `Inspect on GitHub` hero button plus stronger `review, star, or watch` mirror language on the hosted docs homepage so GitHub-native evaluators have an above-the-fold adoption path before install.
  - Commit: `6bc5aad7` — `Strengthen homepage GitHub inspection CTA`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- ralph-workflow/docs/sphinx/index.rst`; `grep -n "Inspect on GitHub\|Review, star, or watch the GitHub mirror" ralph-workflow/docs/sphinx/index.rst`; local build/test unverified in this environment due to missing `sphinx`
  - Why: the site already answers the four core questions well, but GitHub adoption signals are still the weakest public proof point. Putting a GitHub inspection CTA directly in the homepage hero is a cleaner bridge from high-intent site traffic to inspect/star/watch behavior than adding another generic asset.

### RalphWorkflow Trust Path
- **Public finish-receipt trust asset**: Added and pushed a new public guide, `what-a-good-ai-coding-finish-receipt-looks-like`, across the repo docs and hosted Sphinx docs, then surfaced it from README, `START_HERE.md`, docs map, hosted docs homepage, getting-started, and quickstart.
  - Commit: `efcd852d` — `Add finish receipt trust path`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md docs/README.md docs/what-a-good-ai-coding-finish-receipt-looks-like.md ralph-workflow/docs/sphinx/index.rst ralph-workflow/docs/sphinx/getting-started.md ralph-workflow/docs/sphinx/quickstart.md ralph-workflow/docs/sphinx/what-a-good-ai-coding-finish-receipt-looks-like.md`; repo link check (`LINK_CHECK_OK`); local build/test unverified in this environment due to missing `pytest`
  - Why: live workflow pain has shifted from just "can I run more agents?" to "what should the morning-after handoff actually say so I do not have to reconstruct the whole night?" A dedicated finish-receipt page is a more reusable trust/conversion asset than another generic explainer because it turns reviewability into a concrete inspection standard high-intent evaluators can use immediately.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/codex/comments/1t8n9rs/codex_vs_claude_code_my_current_take_after/
- **Comment URL:** https://old.reddit.com/r/codex/comments/1t8n9rs/codex_vs_claude_code_my_current_take_after/omcpyg9/
- **Status:** ✅ Published
- **Notes:** Manual post on workflow/trust comparison thread: Codex vs Claude Code: my current take after watching both mature.
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### RalphWorkflow Distribution
- **Reddit comment**: Posted on `r/codex` thread "Codex vs Claude Code: my current take after watching both mature"
  - URL: https://old.reddit.com/r/codex/comments/1t8n9rs/codex_vs_claude_code_my_current_take_after/omcpyg9/
  - Status: ✅ Published
  - Why: the current bottleneck is distribution-to-GitHub conversion, and this still-relevant comparison thread gave room for a fresh phase-ownership answer plus a direct GitHub inspection path to the free open-source project.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeCode/comments/1stu0cr/people_running_25_coding_agents_what_actually/
- **Comment URL:** https://old.reddit.com/r/ClaudeCode/comments/1stu0cr/people_running_25_coding_agents_what_actually/omcywrd/
- **Status:** ✅ Published
- **Notes:** Manual post on fresh r/ClaudeCode thread: People running 2–5 coding agents: what actually breaks first for you?
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### RalphWorkflow Distribution Infrastructure
- **Reddit autopost cadence + GitHub CTA guardrail**: Upgraded `agents/marketing/reddit_autopost.py` so high-fit `r/ClaudeCode` workflow threads now keep the GitHub mirror CTA while the generator also rejects semantically repetitive concept cadences across the last logged Reddit bodies (not just repeated wording/openers).
  - Verification: `python3 -m unittest agents.marketing.tests.test_reddit_autopost -v`; functional generation check against `seo-reports/reddit_monitor_2026-05-17_2115.md` now yields non-repetitive bodies with `LINK: True` and `REGEN: False` for the top live `r/ClaudeCode` / `Claude -> Codex -> Claude` opportunities
  - Why: the current bottleneck is distribution-to-adoption, and the repeat risk had moved from phrase reuse to whole body cadence. This keeps future Reddit replies fresher while preserving the inspect/star/watch path on the highest-fit workflow threads instead of dropping the GitHub CTA during regeneration.

### RalphWorkflow Conversion / Owned-content Distribution
- **Surfaced strongest write.as essays on primary conversion paths**: Added and pushed a new "deeper workflow argument" section to the public README, `START_HERE.md`, and hosted docs homepage source so existing high-intent visitors can discover the three strongest owned essays (`How to Tell if an AI Coding Task Is Actually Done`, `Claude Code + Codex Workflow: Plan, Build, Review`, `When Unattended AI Coding Actually Works`) instead of leaving those assets undistributed.
  - Commit: `a44db799` — `Surface owned essays on conversion paths`
  - Status: ✅ Pushed to Codeberg and GitHub mirror
  - Verification: `git diff -- README.md START_HERE.md ralph-workflow/docs/sphinx/index.rst`; `grep -nE "How to Tell if an AI Coding Task Is Actually Done|Claude Code \+ Codex Workflow: Plan, Build, Review|When Unattended AI Coding Actually Works" README.md START_HERE.md ralph-workflow/docs/sphinx/index.rst`; local build/test unverified in this environment due to missing `sphinx`
  - Why: the fresh Reddit distribution path was inside the active global cooldown window (`global_cooldown_active:43m_since_last_post`), while the audit still showed published write.as assets sitting with almost no discovery. Surfacing those essays on the repo/docs entry points was the highest-leverage move available immediately because it reuses existing proof/distribution assets, answers the four marketing questions in deeper form, and gives current visitors a stronger trust path without writing more generic content.

### Reddit autopost
- **Thread:** https://old.reddit.com/r/ClaudeAI/comments/1t9tolw/running_two_claude_code_agents_on_the_same_repo/
- **Comment URL:** https://old.reddit.com/r/ClaudeAI/comments/1t9tolw/running_two_claude_code_agents_on_the_same_repo/omddojv/
- **Status:** ✅ Published
- **Notes:** Manual post on worktrees thread: semantic invalidation + shared-boundary owner angle with GitHub link.
- **Retrospective source:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md`

### RalphWorkflow Distribution
- **Reddit comment**: Posted on `r/ClaudeAI` thread "Running two Claude Code agents on the same repo simultaneously. Git worktrees make it work."
  - URL: https://old.reddit.com/r/ClaudeAI/comments/1t9tolw/running_two_claude_code_agents_on_the_same_repo/omddojv/
  - Status: ✅ Published
  - Why: the highest-leverage live move available tonight was still distribution, and `r/ClaudeCode` was inside the recent-community cooldown window. This `r/ClaudeAI` thread let RalphWorkflow answer an active worktree pain from a fresher angle — semantic invalidation, shared-boundary ownership, merged-state checks, and a clean GitHub inspection path — without repeating the same ClaudeCode body cadence.

## 2026-05-17 (Sunday) — Evening Audit 2 — 21:20 UTC
- **Bottleneck unchanged:** conversion to free use / GitHub adoption. GitHub: 0 stars. Codeberg: 9 stars.
- **What's working:** Reddit distribution with fresh varied bodies, pacing guards, autoposter freshness fix. All conversion surfaces strong and surfacing correctly. Four marketing questions answered everywhere. Free OSS framing intact.
- **What's broken:** GitHub adoption still zero despite strong surfaces. write.as articles (May 11-16, 6 published) have zero external distribution — never submitted to HN, Lobsters, or any real-reach platform. Repeated-opening-line risk still alive in autoposter despite prior fix attempt.
- **Diminishing returns confirmed on conversion assets.** Surfaces are done. Stop adding more.
- **Next highest-leverage moves:** (1) Submit strongest write.as articles to HN + Lobsters with GitHub mirror links — only untried distribution channel. (2) Verify GitHub CTA end-to-end injection in Reddit bodies — repeated opening line suggests generator still outputs old line sometimes. (3) Post to strongest fresh thread from latest monitor: "People running 2–5 coding agents: what actually breaks first for you?" (r/ClaudeCode).
- **Rules triggered:** four marketing questions still answered. Free OSS framing preserved. Bottleneck unchanged — no material workflow direction change.

### RalphWorkflow Distribution Infrastructure
- **Reddit autopost anti-repetition upgrade**: Tightened `agents/marketing/reddit_autopost.py` so the generator now looks back across the last 5 logged Reddit bodies, penalizes repeated GitHub CTA paragraphs and repeated sentence-level overlap, and picks from a wider set of category-aware GitHub mirror snippets instead of drifting back to the same product close.
  - Verification: `python3 -m py_compile agents/marketing/reddit_autopost.py agents/marketing/tests/test_reddit_autopost.py`; `python3 -m unittest agents.marketing.tests.test_reddit_autopost -v`; functional generation probe against the latest monitor shortlist now shows `cta_repeat False` / `regen False` on the top live `r/ClaudeCode` and `r/codex` opportunities.
  - Why: the evening audit showed distribution was still the bottleneck, but Reddit body repetition had moved from openers into the GitHub CTA itself. Fixing the generator is higher leverage than another one-off post because it improves future distribution quality while preserving the free/open-source GitHub conversion path.

### RalphWorkflow Distribution Infrastructure
- **HN/Lobsters submission packet refresh**: Replaced the stale non-Reddit submission drafts with a current packet built around the strongest owned trust asset, `How to Tell if an AI Coding Task Is Actually Done` (`https://write.as/7pqpd2y0v0re2.md`), and added fresh dated checklists in `drafts/2026-05-18_hackernews_post.txt` and `drafts/2026-05-18_lobsters_post.txt` with the article URL, GitHub mirror CTA, and channel-specific follow-up angles.
  - Verification: `grep -nE "How to Tell if an AI Coding Task Is Actually Done|GitHub mirror|reviewable" marketing/content-drafts/hackernews.txt marketing/content-drafts/lobsters.txt drafts/2026-05-18_hackernews_post.txt drafts/2026-05-18_lobsters_post.txt`; live endpoint check confirmed HN submit is currently rate-limited from this host (`HTTP 429`) and Lobsters routes `/stories/new` to the login page, so the packets are now the ready-to-fire distribution path for the next authenticated/manual submission.
  - Why: the current bottleneck is still owned-essay distribution into high-intent technical communities, and the existing HN/Lobsters copy was stale, product-heavy, and out of step with the stronger free/open-source trust framing now live across RalphWorkflow. Refreshing the packets keeps the next non-Reddit distribution move aligned to the four marketing questions and gives GitHub-native evaluators a clean inspect/star/watch path from the first comment.
