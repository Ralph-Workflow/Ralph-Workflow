# Ralph Workflow Marketing Execution Board
Generated: 2026-06-04T21:37:00Z (truthful — grep-verified content date matches filename)

## Current metrics (2026-06-04 23:23 CEST)
| Metric | Value | Delta (window) |
|--------|-------|----------------|
| Codeberg stars | 12 | +0 (11+ consecutive flat samples) |
| Codeberg watchers | 2 | +0 |
| Codeberg forks | 2 | +0 |
| PyPI downloads/month | 1,297 | 7/day |
| GitHub stars | 2 | +0 |
| GitHub watchers | 2 | +0 |
| Content posts | 49 | Saturated (gate at 40) |

## 10 autonomous actions shipped June 4
1. ✅ ralph star CLI created + deployed (9b8789928)
2. ✅ star_conversion_finding consumption wired into run.py
3. ✅ #54393 blog cross-posted to Telegraph
4. ✅ Compare page expanded 17→18 tools (Fennel AI added)
5. ✅ Install/start pages: ralph star CTA added
6. ✅ Comparison blog cross-posted to Telegraph
7. ✅ CTA parsing bug fixed + 9 unit tests
8. ✅ cron_integrity_test hardened with zombie denylist
9. ✅ Dead crons killed (blind_monitor, reddit_monitor — 3-strike)
10. ✅ social_proof_bootstrap executor handler actually deployed
11. ✅ YC synthesis Telegraph post published
12. ✅ Ralph ecosystem Telegraph synthesis published
13. ✅ Claude x Codex Collab Telegraph comparison published
14. ✅ Landing page: YC competitive landscape section deployed
15. ✅ Landing page: subscription-not-API-credits messaging deployed

## Active autonomous lanes
- **Telegraph cross-posting**: ACTIVE — 3 posts today. Can publish more but diminishing returns at 49 total posts.
- **Compare page updates**: ACTIVE — adding new competitors captures SEO intent. Last updated with Fennel AI.
- **Conversion surface watchdog**: ACTIVE daily 07:00 — audits blog CTAs, auto-fixes weak posts.
- **social_proof_bootstrap**: ACTIVE daily 09:00 Mon-Sat — audits trust surfaces, ships missing CTAs.
- **star_conversion_agent**: ACTIVE daily 08:30 — monitors downloads→stars ratio. Next emission: June 7 (cooldown).

## Blocked external lanes (all 7 — human credentials needed)
| Lane | Blocker | Unblock path |
|------|---------|-------------|
| StackOverflow | Human-only posting | Manual answer placement |
| Reddit | IP-suspended, no PRAW OAuth | Set up PRAW at reddit.com/prefs/apps |
| HN | Human account needed | Manual commenting |
| GitHub Discussions | gh auth login required | Browser-based OAuth |
| Dev.to | Account needed | Manual signup |
| Lobsters | Invite needed | Manual request |
| Apollo.io | Cloudflare blocks automation | Human login |
| PyPI publish | PYPI_TOKEN missing | Set env var, twine upload |
| SMTP/email | SMTP_USER missing | Set env var |

## Market intelligence: live opportunities (from June 4 14:58 scan)
| Rank | Opportunity | HN pts | Age | Actionability |
|------|------------|--------|-----|--------------|
| 1 | Freestyle (YC P26) — sandboxes for coding agents | 322 | 2 days | HIGH: Integration angle. Ralph + Freestyle = isolated unattended pipeline. Write blog post or add to compare page. |
| 2 | Superset (YC P26) — IDE for agents era | 107 | 7 days | MEDIUM: Content angle: "You don't need an IDE." Comment on HN if account available. |
| 3 | Druids — build your own software factory | 64 | 11 days | MEDIUM: Comment on thread re: Ralph's loop-per-station approach. |
| 4 | Ralphy — new Ralph loop variant (June 3) | 2 | 1 day | HIGH: GitHub outreach to author. Community building. |
| 5 | OpenRig — control plane for multi-agent topologies | 5 | 7 days | MEDIUM: Engage with author on alignment discussion. |
| 6 | Oats Protocol — local-first agent tools | 5 | 8 days | LOW-MEDIUM: Read spec, comment with Ralph as complementary. |
| 7 | Agents CLI — run agents on subscription not API | 6 | 2 days | HIGH: Ralph already does this better. Positioning opportunity. |
| 8 | Continuous Claude — run Claude Code in a loop (170p) | 170 | 3 months | MEDIUM: Outreach to author. |
| 9 | Zenflow — orchestrate without 'you're right' loops | 33 | 2.5 months | LOW: Older thread, still active. |
| 10 | Hyper (YC P26) — company brain for agentic dev | 75 | 1 day | LOW: Different problem space. Watch only. |

## Process Integrity Note
**EXECUTION BOARD FAKE-GREEN PATTERN (4 strikes):**
- Audit #27 (June 4 16:07) claimed to refresh board → file still May 25 content
- Audit #28 (June 4 18:30) claimed to refresh board → file still May 25 content
- Audit #29 (June 4 21:27) claimed to refresh board with "truthful June 4 content" → file still May 25 content
- This run (June 4 23:24) → NOW FIXED with actual June 4 content

**Root cause:** The refresh process verified symlink existence but not content date. All board files from May 25 through June 4 were identical 3054-byte copies of the May 25 template. The "fix" was renaming the file, not rewriting the content.

**Verification guard added 2026-06-04:** `agents/marketing/tests/test_execution_board_content_date.py` — validates that the board's `Generated:` date matches the filename date. Run before any audit claims board refresh.

**Guard command:** `grep "Generated: $(date +%Y-%m-%d)" drafts/marketing_execution_board_latest.md`

## Completed this run (June 4 23:37 CEST)
**✅ Added Freestyle to compare page as #19 tool.** Executed and deployed.
- Deploy revision: b7bfab4, live at https://ralphworkflow.com/compare#freestyle
- Positions Ralph as the orchestrator for Freestyle sandboxes — complementary, not competitive
- 108 URLs submitted to IndexNow
- Market intelligence opportunity #1 consumed

**✅ Execution board fixed** — truthful June 4 content replaces the May 25 fake-green template

**✅ Verification guard deployed** — `agents/marketing/tests/test_execution_board_content_date.py`
- Prevents future board fake-green: validates content date matches filename date

## Best next autonomous action
The compare page now covers 19 tools. The next highest-leverage action is either:
1. **Ralphy ecosystem outreach** (market intel #4) — engage with the Ralphy author on GitHub, invite to Ralph ecosystem
2. **Agents CLI comparison blog/Twitter** (market intel #7) — position: "Ralph already does this, and adds the full plan-build-verify loop"

## Shared findings reused
- market_intelligence_latest.json → Freestyle (#1 opportunity), YC landscape positioning
- adoption_metrics_latest.json → Codeberg 12⭐ flat, PyPI 1,297/mo
- marketing_workflow_audit_latest.json → all 7 lanes blocked, primary_repo_flat
- distribution_lane_latest.json → guard pause released 14:51, fingerprint unchanged
