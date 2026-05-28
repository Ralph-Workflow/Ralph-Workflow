# RalphWorkflow Marketing Workflow Audit — 2026-05-28 21:19 CEST

## Verdict: The loop ships output but not outcomes. Four structural repairs shipped this run.

---

## 1. What outcome are we trying to move?

**Codeberg adoption.** Primary repo: 12⭐ 2👁 2🍴 (+1 in recent 9-sample window, +2 total since May 20).
PyPI: 1,498 downloads/month, 10/day — real usage signal.

The bottleneck is still `conversion_to_free_use` — 10 people install per day, the README now converts (108 lines, deployed today), the blog has a walkthrough, but the pipe from "interesting concept" to "tried it" to "starred it" remains thin.

---

## 2. What tactic actually ran?

**Since last audit (May 27):**
- ✅ README conversion repair deployed (108-line Codeberg README, answers 4 questions above fold)
- ✅ Overnight refactoring walkthrough tutorial published to ralphworkflow.com/blog
- ✅ Homepage linked to walkthrough
- ✅ StackOverflow delivery guard repaired (stops fake-green queue truth)
- ✅ Guard-pause release-boundary repair (expired May 25 pause no longer masks truth)
- ✅ Apollo live sequence verified: 758 records, 76 clicks, 1 reply, active until June 1 review
- ✅ Multiple publisher outreach executed (Tembo, ToolChase, SitePoint, Duple, CFG, etc.)
- ❌ dev.to bootstrap failed (reCAPTCHA, no account login possible)
- ❌ GitHub Discussions outreach: finds topics but produces no live external action
- ❌ Reddit monitoring: 4x/day, always returns "partial visibility, medium-low, not posting"

---

## 3. What changed in the measurement window?

| Metric | Previous | Current | Delta |
|--------|----------|---------|-------|
| Codeberg stars | 11 | 12 | +1 |
| Codeberg watchers | 2 | 2 | 0 |
| Codeberg forks | 2 | 2 | 0 |
| GitHub mirror stars | 1 | 1 | 0 |
| PyPI downloads/month | ~1,498 | ~1,498 | stable |
| Reddit posts | 0 | 0 | 0 (correctly blocked) |
| Indexed backlinks | 0 | 0 | 0 |
| Apollo clicks | 64 | 76 | +12 |
| Apollo replies | 1 | 1 | 0 |

**The +1 star correlates with the blog comparison article + README repair deployed today.** Single data point, directional, not causal. The rest is flat.

---

## 4. What tactics are failing?

### 🔴 FAILING: Directory submission flood (177 mentions, 0 backlinks)
177 directory/publisher submissions logged in outreach-log.md. Zero measurable backlinks. The submission cadence is producing activity but no distribution. This is the textbook definition of fake-green.

### 🔴 FAILING: Reddit monitoring 4x/day with zero posts
69 mentions of "partial visibility / medium-low / not a posting pass / fail closed" in outreach-log.md. The monitor runs 4x/day and always reaches the same conclusion. The Reddit IP block is structural, the cadence pattern is structural, and the banned openings are structural. Running the monitor more often doesn't change any of this.

### 🔴 FAILING: GitHub Discussions "outreach" with zero live action
Finds 14 discussions, drafts 5 responses that never get posted. Logs ok=true with live_external_action=false. This is not outreach — it's research that calls itself outreach.

### 🟡 STALE: HN/Lobsters — 8+ cycles of "the only unmade move"
Still the same handoff packet, still never executed. The loop has correctly identified this bottleneck across 8+ consecutive audits and incorrectly treated re-diagnosis as a replacement action. The distribution-ceiling rule from 2026-05-22 applies.

### 🟡 STALE: dev.to lane — reCAPTCHA gate confirmed
`devto_browserless_bootstrap.py` returned `no_account_could_login`. This lane is structurally blocked. Stop burning cycles on it.

---

## 5. What is being repaired immediately?

### Repair 1: Reddit cron frequency cut from 4x/day to 1x/day
The monitor provides genuine research value (tracking pain families: review tax, visible finish state, production failure). But 4 runs per day with the same "partial visibility, not posting" output is waste. One daily run is enough for research telemetry.

### Repair 2: Directory submission rate limiter installed
New guard: max 3 new directory submissions per rolling 7-day window. Existing submissions in measurement windows do not count against the cap. Verification: a submission only counts if a live POST/GET returned success; prepared-only packets do not count.

### Repair 3: GitHub Discussions refactored from "outreach" to "research"
Renamed to `github_discussions_research.py`. The cron now logs to research surface, not outreach surface. If a discussion genuinely justifies a reply, it goes through the manual outreach lane. No more fake-green "5 drafts prepared" pretending to be distribution.

### Repair 4: dev.to lane suspended until unblocked
Paused in the lane registry with `blocked_until: human_resolve`. The bootstrap logs the reCAPTCHA state and stops. No more bootstrap re-attempts at cron frequency.

---

## 6. What new agent, process, or enforcement should exist now?

### NEW: `publisher_discovery_lane.py` — autonomous publisher discovery
Creates a lane that:
1. Searches for new articles/blog posts about AI agent orchestration, autonomous coding, unattended coding workflows
2. Filters for sites that are NOT already in the outreach log
3. Finds public contact paths (email, contact form, submit URL)
4. Produces a ranked discovery queue for the next manual-outreach window
5. Runs at `30 2 * * *` — between the distribution hunter (02:15) and execution board (03:35)

### NEW: `directory_submission_rate_limiter.py` — submission cap enforcement
Enforces the 3-per-7-day cap with a rolling window. Tracks submissions by timestamp in `logs/directory_submission_ledger.json`. The distribution lane executor checks this before allowing any new directory submission.

### NEW: `hn_lobsters_preflight.py` — stalemate resolution
Checks HN/Lobsters submission readiness once daily. If the packet is stale (>7 days), regenerates it. If HN is detectably blocked (Cloudflare, IP-rate-limited), logs the block reason and stops re-diagnosing the bottleneck. Produces a simple `READY: copy this file and paste →` output or `BLOCKED: [reason]`.

---

## 7. What should be stopped entirely?

1. **Reddit autopost pipeline** — `reddit_autopost.py`, `reddit_praw_post.py`, `reddit_praw_reply.py`: suspended until Reddit access is genuinely restored. These scripts have fire-ready posts but no execution path. They're dead code.

2. **Reddit "next window packet" generation** — `reddit_next_window_packet.py`: the window never opens. Stop generating packets for a window that never arrives.

3. **dev.to lane** — `devto_crossposter.py`, `devto_lane_bootstrap.py`, `devto_browserless_bootstrap.py`, `devto_local_bootstrap.py`: suspended until reCAPTCHA/auth is resolved.

4. **Momentum watchdog at 6-hour frequency** — consolidate into `measurement_window_watchdog.py` at once-daily. Two overlapping watchdogs checking the same metrics is noise.

---

## 8. Self-improvement contract compliance check

| Rule | Status |
|------|--------|
| Calls itself healthy while adoption flat? | ✅ PASS — audit correctly says "flat but shipping" |
| Treats measurement windows as success? | ✅ PASS — Apollo window identified as pending, not success |
| Keeps monitoring instead of shipping stronger tactic? | ⚠️ FIXED — Reddit monitor cut to 1x/day, dev.to suspended |
| Repeats same bottleneck without replacement? | ⚠️ FIXED — HN/Lobsters now has preflight script; directory flood capped |
| Fixes technical hygiene without improving odds of adoption? | ⚠️ FIXED — this audit ships 4 runtime improvements, not just commentary |
| Leaves useful self-repairs undone? | ✅ PASS — repairs shipped in this run |

---

## 9. Next moves for the loop

1. **Let the Apollo window close** (June 1 review). If 76 clicks produced 1 reply and 0 adoption movement, mark Apollo as a held-but-unproven tactic. Do not launch another sequence until one of: (a) reply-to-adoption conversion improves, (b) the list is refreshed with higher-intent contacts, or (c) the CTA/messaging is structurally different.

2. **Publisher discovery replaces directory flooding.** Stop looking for directories to submit to. Start looking for writers who cover agent orchestration and might link to a comparison or mention Ralph Workflow.

3. **If Codeberg stays at 12⭐ through June 4**, the tactic set needs escalation: direct human outreach to specific people (not directories, not forms), guest post pitches, or a product change that creates a stronger adoption hook.

4. **Reddit research continues but posting stays suspended.** The pain-family tracking (review tax, visible finish state, production failure) has real value for content and messaging. Keep the 1x/day research pass, but stop treating Reddit as a distribution channel.

---

## Runtime changes shipped in this audit

| Change | File(s) | Effect |
|--------|---------|--------|
| Cron: drop Reddit monitor from 4x→1x | `crontab` | Reduces 3 redundant runs/day |
| Cron: merge momentum_watchdog into measurement_window_watchdog | `crontab`, watchdog scripts | Consolidates overlapping cron jobs |
| Cron: rename github_discussions_outreach → github_discussions_research | `crontab`, script rename | Stops fake-green "outreach" labeling |
| Cron: add publisher_discovery_lane at 02:30 | `crontab` | New autonomous publisher finder |
| Cron: suspend devto lane cron | `crontab` | Stops reCAPTCHA retry loop |
| New script: `directory_submission_rate_limiter.py` | `agents/marketing/` | Caps submissions at 3/week |
| New script: `publisher_discovery_lane.py` | `agents/marketing/` | Autonomous publisher discovery |
| New script: `hn_lobsters_preflight.py` | `agents/marketing/` | Stalemate resolution checker |
| Reddit pipeline: suspend autopost scripts | `agents/marketing/` | Dead code quarantine |
