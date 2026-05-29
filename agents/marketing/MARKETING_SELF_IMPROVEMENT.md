# Marketing Self-Improvement Contract

## Core rule
The RalphWorkflow marketing system owns **marketing outcomes**, not just marketing activity.

The primary outcome is movement on the **Codeberg** repo first:
- stars
- watches
- forks
- useful issues
- qualified evaluator traffic that turns into public repo signals

Secondary outcomes include:
- GitHub mirror trust signals
- useful backlinks
- meaningful discussion traction
- clearer evaluator understanding on public surfaces

## Default operating stance
If there is any question about whether the marketing system is allowed to act, the default answer is:

**It is up to the system to decide and proceed.**

Do not stop at:
- status reporting
- artifact freshness
- "healthy loop" language
- recommending changes without making them
- discovering the same bottleneck repeatedly without replacing the tactic

## What the marketing system is explicitly allowed to change
When a change is safe and internal, the marketing system should make it in the same run.

This includes:
- creating new marketing agents
- splitting one weak agent into multiple sharper agents
- deleting or retiring stale marketing agents
- rewriting existing agent prompts
- changing cron schedules and job payloads
- patching marketing scripts, audits, watchdogs, and verifiers
- adding tests and checks that prevent fake-green marketing states
- changing development workflow/process when that improves marketing execution quality
- generating new artifacts, drafts, packets, landing-page ideas, and distribution assets
- tightening fail-closed rules when the system is flattering itself

## What counts as failure
The system is failing if it does any of these:
- calls itself healthy while primary repo adoption is flat
- treats measurement windows as success
- keeps monitoring instead of shipping a stronger tactic
- repeats the same bottleneck explanation without a replacement move
- fixes technical hygiene without improving the odds of real distribution or conversion
- leaves useful self-repairs undone because nobody explicitly asked for them

## Required self-improvement loop
Every meaningful marketing audit should answer:
1. What outcome are we trying to move?
2. What tactic actually ran?
3. What changed in the measurement window?
4. If nothing changed, what tactic is now considered failing?
5. What was repaired immediately?
6. What new agent, process, or enforcement should exist now that did not exist before?
7. What should be stopped entirely because it is wasting cycles?

## Escalation rule
If current agents/processes are not enough to move outcomes, the system should:
1. create a better agent
2. repair or replace the current process
3. change the runtime contract
4. only then report what it changed

Do not assume the current agent/process architecture is fixed.

## Marketing-specific interpretation
The job is not just to keep the marketing machine running.
The job is to keep redesigning the marketing machine until it can create better odds of real adoption.

---

## 2026-05-22 structural addendum — distribution ceiling rule

**Finding:** The HN/Lobsters handoff has been the "only remaining high-leverage move" across 7+ consecutive audits without execution. The loop is correctly diagnosing the bottleneck and incorrectly treating the same unmade handoff as a replacement tactic.

**Rule:** When a named bottleneck appears in 3+ consecutive audits without a replacement move being shipped, the system must treat that bottleneck as a **structural ceiling** and create a new distribution lane in the same run. The correct response to "execute HN/Lobsters" on audit #3+ is not another packet rewrite — it is a new distribution approach that can run autonomously or with lighter human friction.

**Current structural findings:**
- Reddit cadence is structurally broken (4-paragraph rhythm confirmed across all 6 recent logged posts). Fresh openings do not fix it. Replacement: `drafts/2026-05-22_reddit_structural_rewrite.md`
- Distribution ceiling: HN/Lobsters execution has been stuck for 7+ cycles. Replacement lane: curator/comparison-page outreach packet at `drafts/2026-05-22_curator_outreach_packet.md`
- The loop should not treat fresh body drafts as a fix for structural cadence failure. It should not treat another HN packet rewrite as a fix for execution ceiling failure.

**Reddit cadence structural rule:** If the next reddit_post_analysis shows a consistent paragraph shape (contrast opener → handoff framing → proof bundle → product close) across posts, Reddit posting must be suspended and replaced by a different distribution approach entirely.

---

## 2026-05-28 structural addendum — directory submission flood rule

**Finding:** 177 directory/publisher submissions logged in outreach-log.md with zero measurable backlinks or adoption movement. The submission cadence produces activity but not distribution. This is textbook fake-green output.

**Rule:** Directory submissions are now rate-limited to 3 new submissions per rolling 7-day window via `directory_submission_rate_limiter.py`. Only submissions that returned HTTP 200/success count against the cap. Prepared-only packets do not count. The execution board must check `--allow` before green-lighting any new directory submission.

**Replacement lane created:** `publisher_discovery_lane.py` — instead of flooding directories, discover and rank fresh publishers writing about AI agent orchestration who might cite, compare, or mention Ralph Workflow. This is a higher-leverage distribution approach because it targets actual writing/editorial attention rather than directory listings that produce zero measurable backlinks.

## 2026-05-28 structural addendum — cron consolidation rule

**Finding:** The Reddit monitor ran 4x/day (via the momentum watchdog at 00/06/12/18 UTC) plus explicit monitor passes. 69 mentions of "partial visibility, not posting" in the outreach log. Running the same monitor more often does not change the conclusion.

**Rule:** Any cron job that consistently produces the same non-actionable output across multiple runs per day must be reduced to once-daily maximum. The momentum watchdog moved to 10:00 UTC (once daily, from 4x/day).

**Lanes suspended this audit:**
- `devto_crossposter.py` / `devto_browserless_bootstrap.py` / `devto_local_bootstrap.py` — reCAPTCHA/auth blocked, no account login possible. Not cron-scheduled; suspended in the lane registry.
- `reddit_autopost.py` / `reddit_praw_post.py` / `reddit_praw_reply.py` / `reddit_next_window_packet.py` — execution path does not exist while Reddit IP-block remains. Dead code; do not execute until access is genuinely restored.
- `github_discussions_outreach.py` — retained at once-daily but renamed conceptually: it finds research surfaces, not outreach. The fake-green `ok=true, live_external_action=false` labeling is misleading.

**New lanes created this audit:**
- `publisher_discovery_lane.py` — autonomous publisher/article discovery (02:30 UTC)
- `hn_lobsters_preflight.py` — stalemate resolution: one check per day (07:00 UTC), produces READY/BLOCKED output
- `directory_submission_rate_limiter.py` — enforcement for the 3-per-7-day cap

## 2026-05-28 structural addendum (21:50 CEST) — Apollo message failure rule

**Finding:** Apollo sequence "Ralph Workflow Seq" has 76 clicks, 1 reply (0.14%), and 193 spam-blocked (19% spam rate out of ~1000 delivered). The current body opens with an abstract claim before identifying who is speaking, uses promotional language patterns that trigger spam classifiers, and lacks explicit opt-out language.

**Rule:** Any Apollo sequence body with >10% spam-block rate is failing and must be replaced with an anti-spam variant before the measurement window closes. The failure is not targeting — 76 clicks from 758 contacts proves targeting is adequate. The failure is body spam-classification.

**A/B variant created:** `drafts/2026-05-28_apollo_ab_variant_packet.md` — personal-question opener, explicit opt-out, 8-line body (down from 12), no bullets, no feature list. If reply rate doesn't improve to 1%+ by the June 8 extended measurement window, switch targeting before touching body again.

## 2026-05-28 structural addendum (21:50 CEST) — HN/Lobsters Show HN pivot rule

**Finding:** HN/Lobsters handoff has appeared as the only remaining high-leverage move across 8 consecutive audit cycles now (was 7 as of May 22). All previous packets were comparison/analysis essays framed for Lobsters or main HN. None were Show HN.

**Rule:** When the same human-gated distribution lane is the bottleneck across 5+ consecutive audits, the system must try a qualitatively different category of post on that same surface before abandoning it. For HN, the categories are: Show HN, Ask HN, main post, and Lobsters story. If Show HN has not been tried, it must be the next attempt.

**Show HN packet created:** `drafts/2026-05-28_show_hn_packet.md` — personal voice, concrete outcome framing, 3-step quick-start, links directly to Codeberg (not Telegraph intermediary). If Show HN is also unposted after the next review window, the HN/Lobsters lane must be marked structurally-blocked and packet-generation effort for it must stop entirely.

## 2026-05-28 structural addendum (21:50 CEST) — Publisher outreach queued, SMTP gate

**Finding:** `publisher_discovery_lane.py` is now production-quality (DDG redirect URL parsing fixed, NoneType crash repaired). Top 5 ranked results all have zero prior outreach. But SMTP is unavailable from this environment (SMTP_USER unset), so email-based outreach cannot execute autonomously.

**Rule:** When SMTP is unavailable, publisher outreach packets should be queued as ready-to-send drafts rather than abandoned. The queue is real work — each packet contains a targeted email body against a specific article — and exists to be actionable the moment SMTP becomes available or can be handed off for manual send.

**Publisher outreach queued:** `drafts/2026-05-28_publisher_outreach_packet.md` — 3 targeted emails (GetStream.io, OpenAgents.org, AppIntent.com), all comparison articles that already cite competitors, zero prior contact.

## 2026-05-29 structural addendum — Dead-cron rule + total search collapse rule

**Finding:** 4 cron jobs consumed resources across this audit window while producing zero autonomous distribution output:
- `run_posting.py` (06:00, 14:00, 22:00 UTC — 3x/day) — always "No scheduled drafts for today"
- `hn_lobsters_preflight.py` (07:00 UTC) — always "BLOCKED — packet_stale" (channel permanently blocked)
- `apollo_sequence_launcher.py` (09:00 UTC) — empty log (zero output ever, Cloudflare-blocked)
- `apollo_outbound_verifier.py` (08:30 UTC) — 334 lines of Cloudflare-blocked status at 17KB log growth

**Rule: Dead-cron self-suppression.** Any cron job that:
- produces the same non-actionable output for 7+ consecutive runs, OR
- runs for a permanently blocked channel, OR
- produces zero output for 3+ consecutive runs

...must be removed from crontab and replaced with a once-daily or once-weekly watchdog check that verifies the underlying blocker hasn't cleared.

**Cron changes executed this run:**
- ✂️ `apollo_sequence_launcher.py` — REMOVED from crontab (permanently blocked, zero output)
- ✂️ `hn_lobsters_preflight.py` — REMOVED from crontab (permanently blocked, single-line output)
- ✂️ `run_posting.py` 14:00 + 22:00 — REMOVED. Kept 06:00 UTC once-daily (Telegraph not blocked, just no daily drafts)
- ✂️ `apollo_outbound_verifier.py` 08:30 — REMOVED from daily crontab. Replaced with once-weekly Monday 08:30 UTC check.

**Structural finding: Total search collapse on web_search provider.** As of 2026-05-29 09:50 CEST, DuckDuckGo web_search is 100% bot-detection blocked and Reddit direct returns 403 IP-blocked. The Reddit monitor has been carrying the same 4-thread shortlist from 2026-05-28 11:19 CEST across 4 consecutive passes. This is not a quiet market — it's a dead search window. The monitor is now reduced to verifying that search collapse continues and carrying forward the most recent healthy shortlist.

**Rule: Total search collapse.** When the web_search provider returns 100% bot-detection failures across 3+ consecutive passes AND the Reddit direct fetch returns 403 IP-blocked:
- The Reddit monitor must cease attempting new queries and carry forward the last healthy shortlist
- Research passes should be reduced to once-daily (already enforced by momentum watchdog at 10:00 UTC)
- The execution board must treat Reddit as a non-distribution research surface only
- No new Reddit packets, drafts, or comment bodies should be generated while search is collapsed (packet generation rule already covers this)

## 2026-05-28 structural addendum (22:30 CEST) — Spidering guard bypass repair (Audit #12)

**Finding:** The spidering guard (`channel_spidering_guard.py`) was wired into `execute_distribution_lane()` and `run.py`, but 6+ standalone lane scripts with their own `main()`/`if __name__ == '__main__':` entry points were invoked via `subprocess.run()` from `marketing_loop_runner.py`, completely bypassing the guard. This caused:
- 6 dev.to bootstrap attempts in 7 minutes (17:44-17:51) despite a permanent stop file
- 3 GitHub Discussions runs in 36 minutes (20:03-20:39) despite a 6h cooldown
- 125 total log files on May 28

**Repairs executed (this run):**
1. **Wired guard into all bypassing scripts** — `devto_browserless_bootstrap.py`, `devto_local_bootstrap.py`, `devto_crossposter.py`, `github_discussions_outreach.py`, `github_discussions_lane.py`, `reddit_retrospective.py`, `reddit_monitor.py`, `reddit_next_window_packet.py`, `hn_lobsters_preflight.py`, `comparison_backlink_executor.py` — all now call `guard_check()` in their `main()` before any work
2. **Fixed dev.to fake-green labeling** — `_log_result()` line 361 hardcoded `live_external_action: False` (was `bool(result.get("ok", False))` which returned `True` when `ok` was string `"False"`)
3. **Marked HN/Lobsters permanently blocked** — stop files written to `channel_blocked/hackernews.txt` and `channel_blocked/lobsters.txt`; added to `PERMANENTLY_BLOCKED` dict in guard. 9+ cycles stalemated triggers the structural ceiling rule (triggered at 3).
4. **Tightened GitHub Discussions cooldown** — from 6h to 24h in `DEFAULT_COOLDOWN_HOURS`. GitHub Discussions is a once-daily research surface, not a distribution lane.
5. **5 channels now permanently blocked with stop files:** dev.to, reddit, smtp-outreach, hackernews, lobsters

**Remaining gap:** `reddit_autopost.py` and `reddit_structural_bodies.py` had their `if __name__` blocks replaced with hard-exit at the top of the file (ARCHITECTURALLY RETIRED). `reddit_praw_post.py`, `reddit_praw_reply.py`, `reddit_post.py`, `reddit_execution_check.py`, `reddit_watchdog.py` — these are not in the `marketing_loop_runner.py` RUN_LIST but should be audited for future bypass risk.

**PyPI update remains the highest-ROI blocked action:** 1,498 downloads/month with stale 0.8.7 README (no Codeberg CTA). The blog posts deployed this cycle are the only autonomous lane producing real external artifacts.

**Log inflation will decay:** Past logs (125 for May 28) cannot be deleted retroactively, but all future accesses to blocked channels will be rejected at the guard level in each standalone script's `main()` before any log generation.

**Finding:** The drafts/ directory now contains 62+ dated comparison/curator/handoff packets, many regenerated within 1-3 days of each other. The execution board has 13 named board lanes but 0 live targets. The system has built more packet queues than it can deliver given human gates on all distribution lanes.

**Rule:** A packet regeneration that does not add materially new distribution targets or qualitatively different outreach angles is fake progress. Before creating a new dated packet for any lane, verify that (a) the existing packet is stale because its targets have changed, not just its date, and (b) the lane has a realistic execution path in the current review window. If neither is true, suppress the packet generation and report the lane as structurally blocked instead.

**Current lanes structurally blocked (human gate, no path from this environment):**
- HN/Lobsters posting
- Apollo sequence editing (Cloudflare/auth block)
- Publisher email outreach (SMTP unavailable)
- Reddit posting (IP-blocked)
- GitHub PR/issues submission (gh CLI not authed)
- dev.to posting (reCAPTCHA block)

**Current lanes that can execute autonomously:**
- README/site copy optimization (conversion surface)
- Blog/owned-content creation and deployment
- Publisher discovery and research
- Adoption metrics collection
- Market intelligence/competitor monitoring
- Packet/draft preparation for human handoff
- Cron job / watchdog / self-repair maintenance

---

## 2026-05-29 structural addendum (09:54 CEST) — Telegraph data bug fix + draft inflation + directory exhaustion

**Finding 1: Telegraph `telegraph_posts.json` was in wrong format.** The file was stored as a raw JSON list `[{...}, {...}]` but the codebase (`crosspost_blog_content()`, `load_posted()`) expected `{"posts": [...], "last_run": ...}` dict format. This caused `already_posted_successfully()` to silently match every blog post as "already posted" because it checked `posted.get("posts", [])` against a list-like iterable, which returned falsy but passed the containment check. Net effect: the 06:00 UTC Telegraph cron silently produced "No scheduled drafts for today" across all 3 daily runs because all 31 blog posts appeared pre-posted.

**Repair executed:** Migrated `telegraph_posts.json` to correct dict format. Backup saved. 10 historical posts preserved. The next 06:00 UTC run will find fresh blog posts to cross-post.

**Finding 2: 53 of 219 drafts were older than 7 days.** HN/Lobsters/Reddit/dev.to drafts from May 11-21 sat in the active folder, inflating the apparent pipeline. Archived to `drafts/archive/2026-05-29/`. Remaining 161 active drafts still contain legitimate working packets.

**Finding 3: Directory submission target pool is exhausted.** All 4 "easy" directories self-reported by `channel_discovery.py` as working (aitoolsindex, codaone, toolshelf, toolwise) were already submitted by the 2026-05-23 cycle. The 4 remaining unsubmitted directories (agentdepot, aisotools, comeai/saatool, saashub) have live submit forms but no programmatic submission path — these require manual HTML form filling. The `distribution_hunter.py` now cycles on `skipped_repair` because there are no fresh directory targets.

**Finding 4: Owned content saturation.** 31 blog posts cover 12 comparisons, 5 practical guides, and a variety of SEO topics. But a gap existed: no standalone evaluator decision guide for "Should I use Ralph Workflow?" This is the most common question from organic traffic and was missing as a first-link destination.

**Content created this run:** `is-ralph-workflow-right-for-your-project-decision-guide.md` — 4-stage decision framework (project fit, team need, prerequisites, failure modes) with concrete signal tests at each stage. Fills the evaluator gap. Links to Codeberg primary + GitHub mirror.

**Content gaps remaining (too large for autonomous creation in this run):**
- CI/CD integration guide (needs working CI pipeline example)
- TCO/cost analysis (needs multi-model pricing research)
- Migration guide from IDE agents (needs migration path validation)
- Security audit/SOC2 discussion (needs compliance expertise)

**Cron self-repair complete for this audit cycle:**
- 4 dead/blocked cron entries removed (apollo_sequence_launcher, hn_lobsters_preflight, 2 run_posting redundancies)
- apollo_outbound_verifier reduced to once-weekly Monday
- Telegraph data format fix unblocks the remaining daily Telegraph cross-post
- `outreach-log.md` updated with full audit findings

**Structural ceiling confirmed, no new autonomous distribution lanes available.** The system has:
- Exhausted directory submissions (all easy targets submitted; remaining 4 need human form-filling)
- Fixed the Telegraph cross-post pipeline (data format bug was the blocker)
- Created evaluator decision content (fills blog content gap)
- Archived stale drafts (53/219 cleaned)
- Eliminated dead cron weight (4 entries removed)

All remaining distribution blockers are human-gated (PyPI token, gh auth login, Apollo Cloudflare solve, SMTP credentials). The autonomous system has maximized what it can do without human intervention.

**PyPI blocker truth (unchanged since 2026-05-28, highest-ROI blocked action):**
- v0.8.8 built, README verified (has Codeberg CTA), but cannot publish without `PYPI_TOKEN`
- 1,428 monthly downloads see v0.8.7 README
- Each download is a conversion opportunity without a star/watch/fork CTA path
