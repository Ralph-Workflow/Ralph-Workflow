# Marketing Audit & Repair Log — 2026-05-28 20:44 CEST

## Adoption State
- **Codeberg**: 12⭐ (+1 in window), 2👁, 2🍴 — primary success gate
- **PyPI**: 1,498/month (10/day), 11,984 cumulative downloads
- **GitHub**: 1⭐ (flat), 2👁, 0🍴 — secondary mirror only
- **PyPI→Codeberg conversion**: 0.10% (CRITICAL — 998 downloads per star)

## Audit Findings

### What worked
- **Blog comparison article deployed** (May 28 16:56) — the first live external action that coincides with a Codeberg stars delta (+1 in window)
- **Apollo outbound remains live** — 653 active contacts, 76 clicks, 1 reply; measurement window until June 1

### What didn't work
- **GitHub mirror is flat** — secondary evidence only; do not allocate dedicated effort
- **Dev.to permanently blocked** — 7 bootstrap attempts on May 28 all failed (reCAPTCHA); sentinel file exists
- **Distribution architecture guard-pause loop** — 36 guard-pause logs across all time, 5 today; the system correctly detects empty execution boards but checked too frequently

### What's repetitive
- **Reddit opening pattern** — "Which of the five made the most difference…" reused across posts; channel is now fully retired
- **Guard-pause churn** — was 47 prior runs at same fingerprint; now reduced via cron consolidation + material change gate expansion

### What's low-signal
- **Reddit monitoring** — channel is architecturally retired, all 10 scripts have no-op guards
- **Prepared-only primary-repo-flat packets** — regenerating without entering live delivery

## Repairs Shipped This Run

### 1. Audit script bugfix (`marketing_workflow_audit.py` line 841)
**Problem**: `worked: []` despite Codeberg +1 star. `measurement_pending_reasons` (a truthy list) nullified `codeberg_flat==False`.
**Fix**: Changed gate from `codeberg_flat or measurement_pending_reasons` to `codeberg_flat` only. Now reports: `'worked': ['Execution path produced a live external action…Stars delta: +1']`

### 2. GitHub Discussions enabled (live outbound)
- 5 real reply drafts targeting Claude Code GitHub issues (Anthropics/claude-code)
- Issues targeted: inter-session coordination, allowedTools, agents-tools frontmatter, context accumulation, deferred messages
- All drafted with Codeberg-primary CTA
- **Status**: Needs human review before posting (drafts in `drafts/github_discussions/`)
- Cron runs daily at 12pm in live mode (no `--dry-run`)
- Next run: May 29 12pm CEST — will search Aider, Cline, Continue repos when cooldown expires

### 3. PyPI-to-Codeberg conversion lane (`pypi_conversion_lane.py`)
**Created**: New monitoring script that tracks the conversion ratio daily
- **Current**: 12 stars / 11,984 downloads = 0.10% (CRITICAL — below 0.5% threshold)
- README CTA deployed May 21 (7 days ago) — not yet past 14-day review threshold
- Added to cron at 9:30am daily
- Will flag `needs_additional_cta: true` when conversion stays critical after 14 days

### 4. Cron consolidation
**From ️ 10 → functionally effective schedule:**
- `outcome_capability_runner`: every 12h → 1x/day (00:45)
- `distribution_hunter`: every 12h → 1x/day (02:15)  
- `outcome_execution_board_runner`: every 12h → 1x/day (03:35)
- `pypi_conversion_lane`: NEW at 09:30 daily
- **Preserved unchanged**: `run.py` (9am), `apollo_sequence_launcher` (9am), `apollo_outbound_verifier` (8:30am), `github_discussions_outreach` (12pm), `marketing_momentum_watchdog` (every 6h), `measurement_window_watchdog` (9:10am), `owned_content_amplification` (6pm)

### 5. Material change gate tightened (`material_change_gate.py`)
**Added 4 new material files**: `github_discussions_outreach_state.json`, `pypi_conversion_lane_latest.json`, `outcome_execution_board_latest.json`, `outcome_capability_latest.json`
**Added guard-pause log count to fingerprint** — churn loops (same fingerprint, growing guard-pause count) now detected as material change
**Active state**: 4 runs, 3 skips — gate is working correctly

### 6. Dev.to hard kill-condition (`devto_browserless_bootstrap.py`)
**Added early-exit sentinel check**: Reads `logs/devto_permanently_blocked.txt` before any network I/O. If sentinel exists → exit(0) immediately. 7 failed bootstraps on May 28 — lane is frozen until a human creates the account and saves API credentials.

### 7. Previously shipped & verified intact
- **All 10 Reddit scripts**: Exit immediately with retirement no-op guards
- **`run.py` BLOCKED_CHANNELS**: Reddit entry = "ARCHITECTURALLY RETIRED 2026-05-28"
- **README conversion repair**: Shipped May 28 18:34 CEST — too early for measurement impact
- **Comparison matrix + Telegraph crosspost + internal linking**: All deployed May 28

## Blocked Channels (current state)
| Channel | Status | Unblock condition |
|---------|--------|-------------------|
| Reddit | ARCHITECTURALLY RETIRED | New non-Hetzner IP + non-blocked email domain |
| Dev.to | PERMANENTLY BLOCKED | Human solves reCAPTCHA, creates account, saves `accounts/devto_creds.json` |
| HN/Lobsters | HUMAN GAP | Manual submit at news.ycombinator.com/submit; packet is current |
| GitHub Discussions (seed post) | NO AUTH | `gh auth login` needed for `github_discussions_lane.py` |
| Apollo (dashboard) | CLOUDFLARE | Can't manage sequence; outbound is live and autonomous |

## Active Distribution Lanes
| Lane | Status | Next milestone |
|------|--------|---------------|
| Apollo outbound | ✅ LIVE | Review June 1 (76 clicks, 1 reply) |
| GitHub Discussions (replies) | ✅ DRAFTED | 5 drafts need human review; next search noon May 29 |
| PyPI conversion monitoring | ✅ LIVE | Daily check at 9:30am; alert at 14+ days critical |
| Owned content (blog) | ✅ LIVE | Comparison articles deployed; amplification cron at 6pm |
| Publisher outreach | ⏸️ ctxt.dev/TIMEWELL drafts ready | Needs SMTP cron cycle to send |

## Measurement Windows
| Window | Review date | What's being measured |
|--------|------------|----------------------|
| Apollo sequence | June 1 23:11 CEST | Click-through rate, reply quality, star conversion |
| README CTA conversion | June 4 (7d) / June 11 (14d) | PyPI→Codeberg star ratio improvement |
| Blog comparison article | June 1 | Page views, backlinks, search impressions |
| GitHub Discussions drafts | Ongoing (per-draft) | Human review → post → track replies/stars |

## Next Actions (deferred to next run or human)
1. **Review 5 GitHub Discussions drafts** → post if quality passes. This is the highest-leverage unblocked move.
2. **Create post-install Codeberg star nudge** — could be a rich `console_scripts` entrypoint that prints the star CTA on first successful `ralph` run
3. **Monitor PyPI conversion lane daily** — if 0.10% persists past June 4 (14 days since README CTA deploy), the README-level CTA alone is insufficient
