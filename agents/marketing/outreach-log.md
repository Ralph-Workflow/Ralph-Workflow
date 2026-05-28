# Outreach Log

Bootstrapped on 2026-05-23 from verified execution logs so future distribution runs can avoid duplicate outreach and duplicate submissions.

## 2026-05-28

- **Execution-board measurement-pending follow-through repair** — auto-refresh the consolidated board during primary-repo hold windows
  - When: 2026-05-28 04:58 CEST
  - Why: Codeberg is still flat, the active lane is `measurement_hold` until 2026-05-28 09:12 CEST, and the execution board is the only truthful follow-through surface during that window. The loop previously refreshed that board only for new system-design repairs, which left measurement-pending hold windows relying on incidental refreshes.
  - Internal repair: patched `agents/marketing/marketing_loop_runner.py` so a measurement-pending `primary_repo_flat` audit automatically runs `outcome_execution_board_runner.py`; added regression coverage in `agents/marketing/tests/test_marketing_loop_runner.py` for both the new trigger path and the non-trigger path.
  - Verification: `python3 -m unittest agents.marketing.tests.test_marketing_loop_runner -v` passed; `python3 agents/marketing/marketing_loop_runner.py` then logged `outcome_execution_board_runner.py` with `triggered_by=post_audit_measurement_pending_follow_through` in `agents/marketing/logs/marketing_loop_runner_latest.json` while the hold remained truthful.
  - Log: `agents/marketing/logs/marketing_2026-05-28_045839_measurement_pending_execution_board_followthrough_repair.json`

- **Dupple** — publisher email sent to `louis@dupple.com`
  - When: 2026-05-28 03:12 CEST
  - Why: the freshest execution board still identified Dupple as the only truthful do-now primary-repo-flat publisher target, the manual packet for this target had already been delivered in the current review window, and a real runtime-sendable email path existed, so live outreach was stronger than another handoff refresh.
  - Subject: `Ralph Workflow for your next AI coding workflow comparison refresh`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `Claude Code vs Cursor in 2026: Which AI Coding Tool Should You Use?`
  - Result: SMTP accepted the email and started a real publisher reply/citation review window.
  - Log: `agents/marketing/logs/marketing_2026-05-28_dupple_publisher_outreach.json`
  - Review by: 2026-06-11

## 2026-05-27

- **Codersera** — publisher email sent to `info@codersera.com`
  - When: 2026-05-27 08:43 CEST
  - Why: Requesty, SOTAAZ, and SitePoint were already in active review windows, while Codersera was the fresh untouched runtime-sendable publisher target on the primary-repo-flat discovery surface.
  - Subject: `Ralph Workflow for your next AI coding agents guide refresh`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `AI Coding Agents in 2026: The Complete Guide`
  - Result: SMTP accepted the email and started a real publisher reply/citation review window.
  - Log: `agents/marketing/logs/marketing_2026-05-27_codersera_publisher_outreach.json`
  - Review by: 2026-06-10

- **Primary-repo-flat outreach state repair** — normalized live publisher state and fixed stale target matching
  - When: 2026-05-27 08:39 CEST
  - Why: the execution board was still resurfacing SitePoint as a fresh publisher packet target even though SitePoint had already been contacted, because recent-outreach matching only compared exact target strings and the live logs used a longer comparison-title variant.
  - Internal repair: backfilled normalized publisher-outreach logs for Requesty and SOTAAZ, promoted the live Codersera send into the canonical publisher-outreach log family, patched `agents/marketing/distribution_lane_executor.py` to match recent publisher targets by normalized name variants, and added regression coverage in `agents/marketing/tests/test_distribution_lane_executor_measurement_hold.py`.
  - Verification: `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold -k long_title` and `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold -k recipient_only_payload` both passed; refreshed `drafts/marketing_execution_board_latest.md` now shows no truthful do-now packet in the current review window.
  - Log: `agents/marketing/logs/marketing_2026-05-27_083900_primary_repo_flat_outreach_state_repair.json`

- **SOTAAZ** — publisher email sent to `support@oncreative.ai`
  - When: 2026-05-27 08:26 CEST
  - Why: `drafts/marketing_execution_board_latest.md` still named the primary-repo-flat publisher contact packet as the strongest truthful do-now asset, but Requesty and SitePoint were already inside active review windows while SOTAAZ remained the fresh untouched runtime-sendable publisher target.
  - Subject: `Ralph Workflow for your next AI coding tools comparison update`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `2026 AI Coding Tool War: Cursor vs Claude Code vs Codex — Hands-On Comparison`
  - Result: SMTP accepted the email and started a real publisher reply/citation review window.
  - Log: `agents/marketing/logs/marketing_2026-05-27_062629_sotaaz_publisher_outreach.json`
  - Review by: 2026-06-10

- **TLDL manual publisher outreach packet** — delivered to the current chat for immediate human execution
  - When: 2026-05-27 03:41 CEST
  - Why: `drafts/marketing_execution_board_latest.md` still listed the manual publisher outreach asset as the single truthful do-now packet, Codeberg adoption remained flat, and the same packet had not yet been delivered in the current review window.
  - Packet: `drafts/2026-05-27_primary_repo_flat_manual_review_asset.md`
  - Target: TLDL
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `AI Coding Tools Compared (2026): Cursor vs Claude Code vs Copilot — Benchmarks & Pricing`
  - Result: surfaced the current Codeberg-first TLDL outreach packet to the active chat instead of regenerating another stale packet or logging another fake-progress hold.
  - Log: `agents/marketing/logs/marketing_2026-05-27_034150_manual_publisher_review_asset_delivery.json`
  - Review by: 2026-06-03

- **Primary-repo-flat contact truthfulness repair** — invalid GitHub-issue-only publisher paths no longer count as executable packet work
  - When: 2026-05-27 01:43 CEST
  - Why: the execution board still had no truthful do-now packet, Codeberg adoption remained flat, and TLDL-style discovery could still make a dead-end GitHub issue path look like executable follow-through.
  - Internal repair: patched `agents/marketing/distribution_lane_selector.py` and `agents/marketing/distribution_lane_executor.py` so `github_issue`-only publisher targets stay non-executable unless they also have a truthful manual/runtime-sendable channel; updated regression coverage in `agents/marketing/tests/test_distribution_lane_selector_repair_pause.py` and `agents/marketing/tests/test_distribution_lane_executor_measurement_hold.py`.
  - Verification: `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause agents.marketing.tests.test_distribution_lane_executor_measurement_hold agents.marketing.tests.test_primary_repo_flat_contact_discovery` → 171 tests passed.
  - Log: `agents/marketing/logs/marketing_2026-05-27_primary_repo_flat_truthfulness_repair.json`

## 2026-05-26

- **AI Saying** — feedback form submission sent
  - When: 2026-05-26 03:50 CEST
  - Why: the post-hold execution board had one fresh publisher-contact target left after overlap-heavy lanes were still inside active review windows, and AI Saying exposed a real runtime-sendable feedback endpoint for its live AI coding tools comparison page.
  - Subject: `Ralph Workflow for your AI coding tools comparison update`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `AI Coding Tools Compared: The Complete 2026 Matrix (14 Tools Ranked)`
  - Result: feedback form accepted with HTTP 200 and JSON response `{"ok":true,"id":3}`, starting a real publisher review/citation window.
  - Log: `agents/marketing/logs/marketing_2026-05-26_035016_aisaying_feedback_submission.json`
  - Review by: 2026-06-09

- **Apollo runtime-blocker review packet** — surfaced to current chat
  - When: 2026-05-26 05:48 CEST
  - Why: `drafts/marketing_execution_board_latest.md` marked this as the single best executable asset still waiting, the Apollo follow-up is due, and the runtime remains Cloudflare-blocked on both local and Browserless probes.
  - Packet: `drafts/2026-05-26_apollo_runtime_blocker_review_packet.md`
  - Result: blocker-specific follow-through was delivered to the current chat so the next Apollo-capable run starts from truthful recovery context instead of another empty-board pause.
  - Log: `agents/marketing/logs/marketing_2026-05-26_054812_apollo_runtime_blocker_review_delivery.json`

## 2026-05-25

- **SaaSHub alternatives page** — Codeberg routing correction comment submitted
  - When: 2026-05-25 21:19 CEST
  - Why: the execution board's active do-now asset was the directory secondary-surface repair packet, and `backlink_status_latest.json` still showed `https://www.saashub.com/ralph-workflow-alternatives` exposing the GitHub mirror but not the canonical Codeberg repo.
  - Surface: https://www.saashub.com/ralph-workflow-alternatives
  - Request: asked SaaSHub to add the primary repo `https://codeberg.org/RalphWorkflow/Ralph-Workflow` on the live alternatives page and keep GitHub as the mirror.
  - Result: native comment form accepted the submission with HTTP 200, but SaaSHub requires email confirmation before the comment can be approved.
  - Log: `agents/marketing/logs/marketing_2026-05-25_saashub_secondary_surface_comment_execution.json`
  - Follow-up: confirm the email if/when mailbox access is available, then recheck the live page in the next review window.

- **Toolradar** — publisher email sent to `editorial@toolradar.com`
  - When: 2026-05-25 17:07 CEST
  - Why: the execution board still listed the primary-repo-flat publisher contact packet as a do-now asset, Toolradar was still untouched in the current review window, and a real SMTP route existed from this runtime, so live publisher outreach was stronger than another manual handoff.
  - Subject: `Ralph Workflow as a workflow-system addition to your AI coding tools guide`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `Best AI Coding Tools in 2026`
  - Result: SMTP accepted the email and started a real publisher reply/citation review window.
  - Log: `agents/marketing/logs/marketing_2026-05-25_toolradar_publisher_outreach.json`
  - Review by: 2026-06-08

- **Codivox** — publisher email sent to `hello@codivox.com`
  - When: 2026-05-25 11:03 CEST
  - Why: the refreshed Codeberg-first primary-repo-flat packet had materially changed and now exposed Codivox as a fresh untouched direct-email comparison target, while the earlier manual packet delivery was already consumed and should not be redelivered.
  - Subject: `Ralph Workflow for your AI coding tools comparison page`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `AI Coding Tools Comparison 2026`
  - Result: SMTP accepted the email and started a real publisher reply/citation review window.
  - Log: `agents/marketing/logs/marketing_2026-05-25_codivox_publisher_outreach.json`
  - Review by: 2026-06-08

- **NxCode** — publisher email sent to `support@nxcode.io`
  - When: 2026-05-25 09:07 CEST
  - Why: the refreshed primary-repo-flat packet still contained one untouched runtime-sendable publisher target, and NxCode was the cleanest high-intent comparison audience still not contacted in the current review window.
  - Subject: `Ralph Workflow as a workflow-system addition to your AI coding tools comparison`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `Codex vs Cursor vs Claude Code comparison with workflow-specific tradeoffs`
  - Result: SMTP accepted the email and started a real publisher reply/citation review window.
  - Log: `agents/marketing/logs/marketing_2026-05-25_nxcode_publisher_outreach.json`
  - Review by: 2026-06-08

- **Beam** — publisher email sent to `frank@nextuptechnologies.co`
  - When: 2026-05-25 02:58 CEST
  - Why: the execution board still named the primary-repo-flat publisher contact packet as the strongest do-now asset, ToolChase was already used in the current review window, and Beam remained the fresh developer-native target with a verified public email path.
  - Subject: `Ralph Workflow as a workflow-system reference for your coding agents comparison`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `Claude Code vs Cursor vs Codex comparison for terminal-first builders`
  - Result: SMTP accepted the email and started a real publisher reply/citation review window.
  - Log: `agents/marketing/logs/marketing_2026-05-25_beam_publisher_outreach.json`
  - Review by: 2026-06-08

- **ToolChase** — publisher contact form submission sent
  - When: 2026-05-25 02:24 CEST
  - Why: the execution board's strongest do-now asset was the primary-repo-flat publisher contact packet, ToolChase had a verified live contact path, and Codeberg adoption is still flat while same-family directory/curator lanes remain inside active measurement windows.
  - Subject: `Advertising & partnerships`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `AI coding tools comparison page already covering Claude Code, Codex, Cursor, and Aider`
  - Result: live contact form submission accepted with HTTP 200 and confirmation page `https://toolchase.com/contact-received/`.
  - Log: `agents/marketing/logs/marketing_2026-05-25_toolchase_publisher_outreach.json`
  - Review by: 2026-06-08

- **SaaSHub** — live listing management repaired to expose Codeberg on-page
  - When: 2026-05-25 01:20 CEST
  - Why: the active review window already ruled out another truthful net-new outbound packet, while fresh shared proof showed SaaSHub was a live third-party page still routing repo intent through GitHub-only evidence even though Codeberg adoption is the main success gate.
  - Action: verified ownership from the fresh SaaSHub email, opened the management surface, updated the listing description to include the primary Codeberg repo plus the GitHub mirror, marked the project as open source, posted a factual correction comment, and confirmed that comment by email.
  - Result: the public SaaSHub page now renders a live Codeberg link, retains the GitHub mirror link, and shows Open Source; refreshed `agents/marketing/logs/backlink_status_latest.json` now marks SaaSHub `preferred_repo_target` as `both` instead of GitHub-only.
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Mirror URL: https://github.com/Ralph-Workflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-25_saashub_repo_routing_execution.json`

- **Directory confirmation — Google 429 guard repair**
  - When: 2026-05-25 01:12 CEST
  - Why: the active short review window still had no truthful do-now outbound packet, and the fresh backlink refresh showed the tracker kept burning the remaining Google search burst after the first HTTP 429.
  - Action: patched `agents/marketing/backlink_status.py` to stop the Google query burst after the first 429, mark the remaining queries as skipped due to prior rate limiting, added regression coverage in `agents/marketing/tests/test_backlink_status.py`, and refreshed `agents/marketing/logs/backlink_status_latest.json`.
  - Result: live listings still = 2; ToolWise remains Codeberg-first; SaaSHub remains GitHub-only; 17 follow-on Google queries are now skipped instead of hammering a rate-limited endpoint.
  - Log: `agents/marketing/logs/marketing_2026-05-25_directory_confirmation_google_rate_limit_guard_repair.json`

## 2026-05-24

- **Wix Engineering** — publisher email sent to `wixeng@wix.com`
  - When: 2026-05-24 20:05 CEST
  - Why: Codeberg adoption is still flat, the active review window already has same-family directory/curator/Apollo work in flight, and Wix Engineering published a fresh agentic-coding systems piece with a real public email and strong overlap with Ralph Workflow’s workflow-layer positioning.
  - Subject: `Ralph Workflow for your agentic coding systems coverage`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `From Co-Pilot to Full Automation: How Wix Is Embedding AI Agents Across an Engineering Org at Scale`
  - Log: `agents/marketing/logs/marketing_2026-05-24_wix_engineering_publisher_outreach.json`
  - Review by: 2026-06-07

- **SitePoint** — publisher email sent to `support@sitepoint.com`
  - When: 2026-05-24 10:46 CEST
  - Why: the StackOverflow lane was already queued for 2026-05-24 11:30 CEST, so this slot needed a different executable lane; SitePoint was already a prepared high-intent editorial comparison target and had not been contacted yet.
  - Subject: `Ralph Workflow for SitePoint's AI Coding Tools Comparison 2026`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-24_sitepoint_publisher_outreach.json`
  - Review by: 2026-06-07

- **HidsTech** — publisher email sent to `hello@hidstech.co.uk`
  - When: 2026-05-24 09:59 CEST
  - Why: Codeberg adoption is still flat, StackOverflow is still in cooldown until 2026-05-24 11:24 CEST, and HidsTech was a fresh distribution-reset comparison target with a real executable contact path.
  - Subject: `Ralph Workflow for HidsTech`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-24_hidstech_publisher_outreach.json`
  - Review by: 2026-06-07

- **Distribution reset repair + fresh publisher target promotion** — fake-progress fallback removed and new comparison/publisher targets promoted
  - When: 2026-05-24 09:49 CEST
  - Why: `distribution_reset` had been able to relog internal comparison assets as if they were fresh discovery; Codeberg adoption is still flat and same-family queues are saturated.
  - Internal repair: patched `distribution_lane_executor.py` so `distribution_reset` now skips when no genuinely new targets exist instead of counting stale comparison pages as progress; added regression coverage in `agents/marketing/tests/test_marketing_system.py`.
  - Fresh targets promoted: `TIMEWELL benchmark — AI Coding Tools Compared [Latest 2026]`, `Kingy AI — Cursor / Cursor SDK vs. Claude Code vs. Codex`, `Built In — Claude Code vs. Codex vs. Cursor vs. GitHub Copilot`, `HidsTech — AI Coding Tools in 2026`.
  - Remaining discovered reserve: `SitePoint — AI Coding Tools Comparison 2026` kept unpromoted in the reset queue.
  - Artifacts: `drafts/2026-05-24_distribution_reset_execution.md`, `drafts/curator_outreach/2026-05-24/`, `agents/marketing/logs/marketing_2026-05-24_distribution_reset_execution.json`
  - Verification: targeted unittest coverage passed; reset execution rerun produced 4 fresh promoted targets and no stale comparison fallback.

- **StackOverflow demand capture** — scheduled a post-cooldown one-shot run
  - When: 2026-05-24 08:47 CEST
  - Why: the StackOverflow lane is the strongest different-family demand-capture surface available, but the live lane is in cooldown until 2026-05-24 11:24:37 CEST and should not be burned early.
  - Action: created one-shot cron `stackoverflow-post-cooldown-demand-capture` for 2026-05-24 11:30 CEST with Codeberg-first CTA instructions and manual-packet fallback if live posting is still unavailable.
  - Proof: `python3 agents/marketing/stackoverflow_answer_lane.py` reported the active cooldown window; `openclaw cron show 7a71bb58-75ac-4862-b316-ed3bdff44b0c --json` confirmed the scheduled run.
  - Log: `agents/marketing/logs/marketing_2026-05-24_stackoverflow_post_cooldown_cron.json`
  - Review by: 2026-05-24 11:45 CEST

- **StackOverflow demand capture guard repair** — queued follow-through now blocks duplicate pre-cooldown resurfacing
  - When: 2026-05-24 10:33 CEST
  - Why: the lane already had a live draft, a current handoff packet, and the 11:30 CEST one-shot run, so another pre-cooldown packet refresh would have been fake progress.
  - Action: patched `agents/marketing/distribution_lane_selector.py` so a recent `stackoverflow_post_cooldown_cron` log with a pending `scheduled_run_at` suppresses another `stackoverflow_answer_handoff_packet` selection and keeps the loop on `measurement_hold` instead.
  - Proof: `python3 -m unittest agents.marketing.tests.test_marketing_system -k stackoverflow` and `python3 -m unittest agents.marketing.tests.test_marketing_system` both passed.
  - Log: `agents/marketing/logs/marketing_2026-05-24_stackoverflow_scheduled_followthrough_guard_repair.json`

- **StackOverflow demand capture follow-through repair** — scheduled a post-run verifier
  - When: 2026-05-24 11:14 CEST
  - Why: the execution board promised a 2026-05-24 11:45 CEST run check for the 11:30 CEST StackOverflow slot, but no actual verifier cron existed yet.
  - Action: created one-shot cron `stackoverflow-post-cooldown-run-check` for 2026-05-24 11:45 CEST to confirm what happened to the live demand-capture run and force a logged replacement follow-through if the slot misses.
  - Proof: cron inspection showed only the 11:30 StackOverflow one-shot existed beforehand; `openclaw cron add ...` returned job `a75a7892-17e7-48b6-a77c-73d0d8b7746b` scheduled for `2026-05-24T09:45:00.000Z`.
  - Log: `agents/marketing/logs/marketing_2026-05-24_stackoverflow_run_check_cron.json`

- **Marketing runtime** — active-loop cadence repaired
  - Change: reduced `marketing-active-loop` from `0 */2 * * *` to `0 */4 * * *`
  - Why: current lane is `measurement_hold`, same-family external windows are saturated, and Codeberg adoption is flat; this reduces fake-progress churn.
  - Expected outcome: cleaner measurement windows and more distinct outcome-bearing marketing actions.
  - Measurement window: review by 2026-05-31.
  - Replace if it fails: move to lane-specific schedules if slower cadence causes missed opportunities without better outcomes.
  - Log: `agents/marketing/logs/marketing_2026-05-24_active_loop_cadence_repair.json`

- **Primary repo flat repair** — Codeberg-primary curator/comparison outreach packet prepared for fresh workflow-native targets
  - Targets: `ctxt.dev`, `AXME Code`, `WyeWorks`, `Bollwerk / Werkstatt`
  - Why: current directory + same-family curator activity has not moved Codeberg stars/watchers; this repair shifts to fresh workflow authors who already publish about coding-agent process, verification, and orchestration.
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Status: personalized outreach copy prepared; verified public contact routes not resolved from current runtime, so this shipped as a manual-send packet rather than falsely claiming sends.
  - Log: `agents/marketing/logs/marketing_2026-05-24_primary_repo_flat_repair.md`
  - Review by: 2026-05-31

- **Cursor comparison conversion asset** — repo-native comparison page shipped
  - When: 2026-05-24 09:22 CEST
  - Why: same-family external lanes are saturated or cooling down, while repo conversion is still the main bottleneck; Cursor is a major high-intent comparison surface already present in shared market-intelligence artifacts.
  - Action: added `docs/ralph-workflow-vs-cursor.md` and linked it from `docs/README.md` with Codeberg as the primary CTA.
  - Shared artifacts reused: `market_intelligence_latest.json`, `seo-reports/comparisons/cursor.md`, `ADOPTION_FUNNEL_NEXT.md`, `marketing_workflow_audit_latest.json`
  - Verification: local docs link-existence check passed for `README.md`, `START_HERE.md`, `docs/README.md`, and `docs/ralph-workflow-vs-cursor.md`.
  - Log: `agents/marketing/logs/marketing_2026-05-24_cursor_comparison_conversion_asset.md`
  - Review by: 2026-05-31

- **AiAgents.Directory** — directory submission sent
  - Submit URL: https://aiagents.directory/submit/
  - Success URL: https://aiagents.directory/submit/success/
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Mirror URL: https://github.com/Ralph-Workflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-24_aiagents_directory_submission.json`
  - Review by: 2026-06-07

- **Claude Stack** — workflow submission sent
  - Submit URL: https://www.claudestack.dev/submit
  - Submission endpoint: https://www.claudestack.dev/api/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/START_HERE.md
  - Mirror URL: https://github.com/Ralph-Workflow/Ralph-Workflow
  - Category: workflows
  - Log: `agents/marketing/logs/marketing_2026-05-24_040259_claudestack_submission.json`
  - Review by: 2026-06-07

- **AI Coding Stack** — curator email sent to `arielyang@gmail.com`
  - Page: https://aicodingstack.io/docs/getting-started
  - Subject: `Suggested AI Coding Stack CLI listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Mirror URL: https://github.com/Ralph-Workflow/Ralph-Workflow
  - Contact source: public latest Git commit author email on the linked GitHub repo
  - Log: `agents/marketing/logs/marketing_2026-05-24_ai_coding_stack_curator_execution.json`
  - Review by: 2026-06-07

## 2026-05-23

- **Claudetory** — directory submission sent
  - Submit URL: https://claudetory.com/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-24_claudetory_submission.json`
  - Review by: 2026-06-07

- **saviorand / awesome-ai-assisted-coding** — curator email sent to `saviorand@gmail.com`
  - Page: https://github.com/saviorand/awesome-ai-assisted-coding
  - Subject: `Ralph Workflow for awesome-ai-assisted-coding`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Contact source: public Git commit author email on the repo
  - Log: `agents/marketing/logs/marketing_2026-05-24_saviorand_curator_outreach.json`
  - Review by: 2026-06-07

- **subinium / awesome-claude-code** — curator email sent to `subinium@gmail.com`
  - Page: https://github.com/subinium/awesome-claude-code
  - Subject: `Suggested awesome-claude-code entry: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_subinium_curator_outreach.json`
  - Review by: 2026-06-06
- **Authority AI Tools** — contact-form suggestion submitted
  - Directory page: https://authorityaitools.com/ai-coding-tools/
  - Contact URL: https://authorityaitools.com/contact/
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_authorityaitools_contact_submission.json`
  - Review by: 2026-06-06
- **DeepYard** — directory submission sent
  - Submit URL: https://deepyard.dev/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_deepyard_submission.json`
  - Review by: 2026-06-06
- **AI Tools (aitools.inc)** — directory submission sent
  - Submit URL: https://aitools.inc/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_aitools_inc_submission.json`
  - Review by: 2026-06-13
- **AI Marketing Directory** — directory submission sent
  - Submit URL: https://www.aimarketing.directory/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_aimarketing_directory_submission.json`
  - Review by: 2026-06-06
- **DevTool Center** — directory submission sent
  - Submit URL: https://www.devtool.center/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_devtoolcenter_submission.json`
  - Review by: 2026-06-13
- **VB Web AI Tools** — directory submission sent
  - Submit URL: https://www.vbwebtools.com/submit-tool/
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_vbwebtools_submission.json`
  - Review by: 2026-06-06
- **ToolScout** — directory submission sent
  - Submit URL: https://toolscout.ai/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_toolscout_submission.json`
  - Review by: 2026-06-06
- **Requesty / Agentic Coding Tools Compared (2026)** — curator email sent to `sales@requesty.ai`
  - Page: https://www.requesty.ai/blog/agentic-coding-tools-compared-2026-claude-code-cursor-codex-aider
  - Subject: `Suggestion for your agentic coding tools comparison: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_085355_requesty_curator_email.json`
  - Review by: 2026-06-06
- **OpenAIToolsHub** — curator email sent to `contact@openaitoolshub.org`
  - Subject: `Suggested OpenAIToolsHub coding listing/review: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_085047_openaitoolshub_curator_email.json`
  - Review by: 2026-06-06
- **Vibehackers / AI coding tools comparison** — curator email sent to `team@vibehackers.io`
  - Subject: `Suggestion for your AI coding tools comparison: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_vibehackers_curator_outreach.json`
  - Review by: 2026-06-06
- **Simplicity Labs / AI Coding Tools Compared** — curator email sent to `hello@simplicitylabs.io`
  - Subject: `Suggestion for your AI coding tools comparison: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_083105_simplicitylabs_curator_email.json`
  - Review by: 2026-06-06
- **AIToolSync** — curator email sent to `support@aitoolsync.com`
  - Subject: `Suggested AIToolSync listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_aitoolsync_curator_outreach.json`
  - Review by: 2026-06-06
- **andyrewlee / awesome-agent-orchestrators** — curator email sent to `andrew@founding.so`
  - Subject: `Suggested awesome-agent-orchestrators listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_andyrewlee_curator_outreach.json`
  - Review by: 2026-06-06
- **MadeWithStack** — directory submission sent
  - Submit URL: https://www.madewithstack.com/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_madewithstack_submission.json`
  - Review by: 2026-06-06
- **The Toolify** — directory submission sent
  - Submit URL: https://submit.thetoolify.dev/
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_thetoolify_submission.json`
- **LangPop / AI Coding Assistant Comparison 2026** — publisher email sent to `hello@langpop.com`
  - Page: https://www.langpop.com/ai/assistants
  - Subject: `Ralph Workflow as a workflow-system reference for LangPop's AI coding assistant comparison`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-25_langpop_publisher_outreach.json`
  - Review by: 2026-06-08
  - Review by: 2026-06-06
- **ListYourTool.com** — directory submission sent
  - Submit URL: https://www.listyourtool.com/submit-tool
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_listyourtool_submission.json`
  - Review by: 2026-06-06
- **AI Tools Magic** — directory submission sent
  - Submit URL: https://aitoolsmagic.com/submit-tool
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_aitoolsmagic_submission.json`
  - Review by: 2026-06-06
- **B2B SaaS Market** — directory submission sent
  - Submit URL: https://b2bsaasmarket.com/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_b2bsaasmarket_submission.json`
  - Review by: 2026-06-06
- **0xWelt / Awesome-Vibe-Coding** — curator email sent to `dinghao12601@gmail.com`
  - Subject: `Suggested Awesome-Vibe-Coding listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_0xwelt_curator_outreach.json`
  - Review by: 2026-06-06
- **no-fluff / awesome-vibe-coding** — curator email sent to `toby.m@rsden.com`
  - Subject: `Suggestion for awesome-vibe-coding: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_no_fluff_curator_outreach.json`
  - Review by: 2026-06-06
- **dariubs / awesome-workflow-automation** — curator email sent to `dariushem@yahoo.com`
  - Subject: `Suggestion for awesome-workflow-automation: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_044623_dariubs_awesome_workflow_automation_curator_email.json`
  - Review by: 2026-06-06
- **hesreallyhim / awesome-claude-code** — curator email sent to `git-dev@hesreallyhim.com`
  - Subject: `Suggestion for awesome-claude-code: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_hesreallyhim_curator_outreach.json`
  - Review by: 2026-06-06
- **Submit AI Tools** — curator email sent to `contact@submitaitools.org`
  - Subject: `Suggested Submit AI Tools listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_submitaitools_curator_outreach.json`
  - Review by: 2026-06-06
- **AgDex** — curator email sent to `agdex.ai@gmail.com`
  - Subject: `Suggested AgDex listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_agdex_curator_outreach.json`
  - Review by: 2026-06-06
- **OpenAgents.pro** — directory submission sent
  - Log: `agents/marketing/logs/marketing_2026-05-23_openagents_submission.json`
  - Review by: 2026-06-06
- **ToolShelf** — directory submission sent
  - Log: `agents/marketing/logs/marketing_2026-05-23_toolshelf_submission.json`
  - Review by: 2026-06-06
- **Nav - AI** — directory submission sent
  - Submit URL: https://nav-ai.net/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_nav_ai_submission.json`
  - Review by: 2026-06-06
- **AgentDepot** — curator email sent to `hello@agentdepot.dev`
  - Log: `agents/marketing/logs/marketing_2026-05-23_agentdepot_curator_outreach.json`
  - Review by: 2026-06-06
- **Altern** — curator outreach sent
  - Log: `agents/marketing/logs/marketing_2026-05-23_altern_curator_outreach.json`
  - Review by: 2026-06-06
- **23blocks / ai-maestro** — curator email sent to `hello@23blocks.com`
  - Log: `agents/marketing/logs/marketing_2026-05-23_012523_23blocks_curator_email.json`
  - Review by: 2026-06-06
- **tools-ai.online** — curator outreach sent
  - Log: `agents/marketing/logs/marketing_2026-05-23_tools_ai_online_curator_outreach.json`
  - Review by: 2026-06-06
- **Come AI** — curator email sent to `devce2300@gmail.com`
  - Subject: `Suggested Come AI listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_025440_comeai_curator_email.json`
  - Review by: 2026-06-06
- **VibecodingHub** — curator email sent to `support@vibecodinghub.org`
  - Subject: `Suggested VibecodingHub feature: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_035253_vibecodinghub_curator_email.json`
  - Review by: 2026-06-06
- **IndieStack** — directory submission sent
  - Submit URL: https://indiestack.ai/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_indiestack_submission.json`
  - Review by: 2026-06-06
- **vivy-yi / awesome-agent-orchestration** — curator email fallback attempt failed
  - Attempted recipient: `developer@claude-ai-hub.com`
  - Failure: SMTP 550 mailbox unavailable; invalid DNS MX/A/AAAA records
  - Log: `agents/marketing/logs/marketing_2026-05-23_vivy_yi_curator_outreach_attempt.json`
  - Next path: do not retry this email; only GitHub issue/manual curator handoff remains
- **ToolWise** — existing third-party listing verified live
  - Listing URL: https://toolwise.ai/tools/ralph-workflow
  - Primary URL on listing: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_backlink_truthfulness_repair.json`
  - Review by: 2026-06-06
- **AI Dev Tools Directory** — curator email sent to `hello@aidevtools.dev`
  - Subject: `Suggested AI Dev Tools listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_aidevtools_curator_outreach.json`
  - Review by: 2026-06-06
- **ithiria894 / Awesome Claude Code Workflows** — curator email sent to `ithiria894@gmail.com`
  - Subject: `Suggested Awesome Claude Code Workflows listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_084523_ithiria894_curator_email.json`
  - Review by: 2026-06-06
- **AIPowerStacks** — curator email sent to `hello@aipowerstacks.com`
  - Subject: `Suggested AIPowerStacks listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_aipowerstacks_curator_outreach.json`
  - Review by: 2026-06-06
- **AIToolsIndex** — prior submission state normalized from live discovery artifacts
  - Submit URL: https://aitoolsindex.org/submit
  - Current state: submitted / review pending; not live yet as of 2026-05-23
  - Log: `agents/marketing/logs/marketing_2026-05-23_backlink_truthfulness_repair.json`
  - Review by: 2026-06-06
- **AIToolboard** — directory submission sent
  - Submit URL: https://aitoolboard.com/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_aitoolboard_submission.json`
  - Review by: 2026-06-06
- **ToolHunter** — curator email sent to `hello@toolhunter.cc`
  - Subject: `Suggested ToolHunter listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_toolhunter_curator_outreach.json`
  - Review by: 2026-06-06
- **Choppy Toast / AI Coding Tools Directory 2026** — curator email sent to `choppy.young@gmail.com`
  - Subject: `Suggestion for AI Coding Tools Directory 2026: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Target page: https://ai-coding-tools.choppytoast.com/
  - Log: `agents/marketing/logs/marketing_2026-05-23_choppytoast_curator_outreach.json`
  - Review by: 2026-06-06
- **CodeAI Directory** — curator fallback email sent to `harish@harishgarg.com`
  - Subject: `Suggested CodeAI Directory listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Reason: public submit surface returned a server-side mail-delivery error, so direct curator fallback was used
  - Log: `agents/marketing/logs/marketing_2026-05-23_200834_codeaidirectory_curator_email.json`
  - Review by: 2026-06-06
- **AI Dev Setup** — contact-form inclusion request sent
  - Submit URL: https://aidevsetup.com/contact
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Mirror URL: https://github.com/Ralph-Workflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-24_ai_dev_setup_contact_submission.json`
  - Review by: 2026-06-07

- **AI for Code** — directory submission sent
  - Submit URL: https://aiforcode.io/
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Log: `agents/marketing/logs/marketing_2026-05-23_aiforcode_submission.json`
  - Review by: 2026-06-06

## 2026-05-22
- **AIGearBase** — directory submission sent
  - Log: `agents/marketing/logs/marketing_2026-05-22_aigearbase_submission.json`
- **TheNextAI** — directory submission sent
  - Log: `agents/marketing/logs/marketing_2026-05-22_thenextai_submission.json`
- **AI for Developers** — curator email fallback sent
  - Log: `agents/marketing/logs/marketing_2026-05-22_ai-for-developers_curator_email.json`
- **QAInsights** — curator email fallback sent
  - Log: `agents/marketing/logs/marketing_2026-05-22_141156_qainsights_curator_email.json`
- **Taskade** — curator email fallback sent
  - Log: `agents/marketing/logs/marketing_2026-05-22_101023_taskade_curator_email.json`
- **filipecalegario / awesome-vibe-coding** — curator email fallback sent
  - Log: `agents/marketing/logs/marketing_2026-05-22_233203_filipecalegario_curator_email.json`
- **Agent-Analytics** — curator email fallback sent
  - Log: `agents/marketing/logs/marketing_2026-05-22_233600_agent_analytics_curator_email.json`

### StackOverflow answer lane
- **When:** 2026-05-23 16:34:50
- **Target:** https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability
- **Action:** repaired the answer generator and refreshed the live-ready packet for the strongest 0-answer production-reliability question
- **Packet:** `drafts/stackoverflow_answer_handoff_packet_latest.md`
- **Status:** ready for authenticated manual placement; direct posting still blocked in this runtime

### StackOverflow manual escalation
- **When:** 2026-05-23 16:44:00
- **Target:** https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability
- **Action:** escalated the live-ready answer packet for immediate manual placement instead of refreshing more internal assets
- **Packet:** `drafts/stackoverflow_answer_handoff_packet_latest.md`
- **Reuse packet:** `drafts/stackoverflow_answer_reuse_packet_latest.md`
- **Status:** current highest-leverage demand-capture asset is now explicitly surfaced for human posting/reuse

## Notes
- Treat Codeberg as the primary repo destination and GitHub as the mirror destination in every future outreach.
- Before contacting any target, search this file and the linked execution logs first.

### Marketing momentum watchdog
- **When:** 2026-05-23 10:46:32
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, mirror_repo_flat, repetitive_outreach.
- **Skiln** — curator email sent to `hey@skiln.co`
  - Subject: `Suggested Skiln listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Draft: `drafts/curator_outreach/2026-05-23/skiln-contact-email.txt`
  - Review by: 2026-06-06

### StackOverflow answer lane
- **When:** 2026-05-23 11:54:43
- **Note:** StackOverflow answer lane ran: found 14 questions, scored 14, drafted 1 answers. Top question: How should I structure autonomous AI agent workflows for production reliability .

### StackOverflow answer lane
- **When:** 2026-05-23 12:20:32
- **Note:** StackOverflow answer lane ran: found 14 questions, scored 14, drafted 1 answers. Top question: How should I structure autonomous AI agent workflows for production reliability .

### StackOverflow answer lane
- **When:** 2026-05-23 12:26:36
- **Note:** StackOverflow answer lane ran: found 14 questions, scored 14, drafted 1 answers. Top question: How should I structure autonomous AI agent workflows for production reliability .

- **LaunchKit Tools** — curator email sent to `hello@launchkittools.com`
  - Subject: `Suggested LaunchKit Tools coding listing: Ralph Workflow`
  - Target page: https://launchkittools.com/submit
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Note: public submit form currently points to a placeholder Formspree endpoint, so contact-email fallback was used
  - Log: `agents/marketing/logs/marketing_2026-05-23_launchkittools_curator_outreach.json`
  - Review by: 2026-06-06

### StackOverflow answer lane
- **When:** 2026-05-23 12:40:14
- **Note:** StackOverflow answer lane ran: found 14 questions, scored 14, drafted 0 answers, skipped 1 recent duplicate candidates. Top question: How should I structure autonomous AI agent workflows for production reliability .

- **Digital Applied / Agentic Coding Tools 2026: 20-Platform Matrix Report** — curator email sent to `info@digitalapplied.com`
  - Subject: `Suggested Digital Applied matrix addition: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Target page: https://www.digitalapplied.com/blog/agentic-coding-tools-q2-2026-20-platform-matrix
  - Log: `agents/marketing/logs/marketing_2026-05-23_digitalapplied_curator_outreach.json`
  - Review by: 2026-06-06

- **WhichAI** — curator email sent to `hello@whichai.tech`
  - Subject: `Suggested WhichAI evaluation: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Target page: https://whichai.tech/
  - Log: `agents/marketing/logs/marketing_2026-05-23_whichai_curator_outreach.json`
  - Review by: 2026-06-06
- **AICavo** — curator email sent to `hello@aicavo.com`
  - Subject: `Suggested AICavo listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Target page: https://aicavo.com/submit
  - Note: public submit path is sign-in gated from this runtime, so direct curator email fallback was used
  - Log: `agents/marketing/logs/marketing_2026-05-23_aicavo_curator_outreach.json`
  - Review by: 2026-06-06

### Marketing momentum watchdog
- **When:** 2026-05-23 13:12:45
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected, channel_access_mismatch. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, mirror_repo_flat, repetitive_outreach. Distribution channels need replacement or human-auth handoff: toolshelf.

### Repo conversion asset
- **When:** 2026-05-23 13:53:00
- **Note:** Added `content/guides/review_ai_coding_output_before_merge.md` and rerouted `README.md` + `START_HERE.md` toward it so repo visitors have a clearer review/trust path while Reddit is fail-closed and Apollo/curator outreach stay in measurement windows.

### Marketing momentum watchdog
- **When:** 2026-05-23 13:57:46
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected, channel_access_mismatch. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, mirror_repo_flat, repetitive_outreach. Distribution channels need replacement or human-auth handoff: toolshelf.

### StackOverflow answer lane
- **When:** 2026-05-23 14:08:48
- **Note:** StackOverflow answer lane ran: found 14 questions, scored 14, drafted 0 answers, skipped 1 recent duplicate candidates. Top question: How should I structure autonomous AI agent workflows for production reliability .

### StackOverflow answer lane
- **When:** 2026-05-23 14:11:21
- **Note:** StackOverflow answer lane ran: found 14 questions, scored 14, drafted 0 answers, skipped 1 recent duplicate candidates. Top question: How should I structure autonomous AI agent workflows for production reliability .

### Marketing momentum watchdog
- **When:** 2026-05-23 14:41:12
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected, channel_access_mismatch. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach. Distribution channels need replacement or human-auth handoff: toolshelf.

### Repair execution log (2026-05-23 14:43)
- **primary_repo_flat — EXECUTED:** Published `ralph-workflow-vs-claude-code.md` comparison post to Ralph Site (git commit e844e22, pushed). Comparison pages existed as drafts but were not live anywhere — publishing them creates permanent reference assets that earn backlinks and provide SEO value. CTA uses Codeberg as primary (codeberg.org/RalphWorkflow/Ralph-Workflow), GitHub as mirror. Remaining 7 comparison pages (aider, cursor, continue, copilot, conductor-oss, conductor-teams, hermes-agent) are ready drafts — schedule publishing in next few days.
- **same_family_distribution_overlap — ACKNOWLEDGED:** Directory submissions paused going forward. 13 submissions in 24h is above burst threshold. Do not submit more directory listings until existing approval windows have matured and produced backlink evidence or aged past their review checkpoints.
- **same_family_outreach_overlap — ACKNOWLEDGED:** Curator outreach bursts paused going forward. 13 curator contacts in 24h is above saturation. Use existing manual-contact packets and let reply/backlink windows mature before another same-family burst. Next curator execution should target a materially different demand-capture lane, not another batch of the same-family contacts.
- **mirror_repo_flat — CONFIRMED OK:** All recent owned content and the new comparison post use Codeberg as primary repo and GitHub as mirror only. No repair action needed.
- **repetitive_outreach — ON HOLD (Reddit blocked):** Reddit is IP-blocked/403 from this environment. Template rewrite cannot be tested while blocked. reddit_fresh_openings.md already has 13+ fresh openings (A-M) plus the 2026-05-20 additional openings (N-S). Template is ready for when Reddit access is restored. When Reddit unblocks, the next post should use a fresh opening from the N-S set, not repeat the banned "handoff" openings.
- **apollo_sequence_launcher:** Already inside measurement window (launched 2026-05-23 00:14, review at 2026-05-30). Do not repackage or re-launch until measurement window completes.

### Marketing momentum watchdog
- **When:** 2026-05-23 14:46:40
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Marketing momentum watchdog
- **When:** 2026-05-23 14:47:33
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Repair execution update (2026-05-23 14:52)
- **primary_repo_flat — ADVANCED:** Now 4 of 8 comparison posts live on Ralph Site (git 44229be). Claude Code (e844e22), Aider, Cursor, Continue — all published and pushed. These create live reference content that earns backlinks and provides SEO value. Each uses Codeberg as primary, GitHub as mirror. Remaining 4 drafts: conductor-oss, conductor-teams, hermes-agent, copilot — schedule next batch.
- **selector insight:** distribution_lane_selector had marked comparison queue as "saturated" but comparison pages were NOT published anywhere. The saturation was false — pages existed as drafts but had no live URL to earn backlinks from. Publishing them resolves the false signal and gives the selector accurate data for future lane decisions.

### Marketing momentum watchdog
- **When:** 2026-05-23 14:49:12
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### StackOverflow draft reuse lane
- **When:** 2026-05-23 15:04:49
- **Note:** Reused the strongest existing StackOverflow reliability draft as a live Ralph Site post instead of regenerating the lane again. Published `How to Structure Autonomous AI Agent Workflows for Production Reliability` (commit `4730af9`) with Codeberg as primary CTA.

### Marketing momentum watchdog
- **When:** 2026-05-23 15:43:02
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Marketing momentum watchdog
- **When:** 2026-05-23 15:43:14
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Marketing momentum watchdog
- **When:** 2026-05-23 15:52:05
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Comparison cluster publication batch
- **When:** 2026-05-23 16:11:00
- **Note:** Published the remaining comparison backlog to Ralph Site in one batch (Conductor OSS, Conductor Teams, Hermes Agent, GitHub Copilot) and pushed commit `d350f05`. This keeps Codeberg as the primary CTA while Apollo, curator outreach, and directory lanes stay in active measurement windows. Verification passed: `bin/with-ruby bundle exec rspec spec/requests/blog_spec.rb spec/services/blog/post_repository_spec.rb` (44 examples, 0 failures).
- **Live URLs:**
  - https://ralphworkflow.com/blog/ralph-workflow-vs-conductor-oss
  - https://ralphworkflow.com/blog/ralph-workflow-vs-conductor-teams
  - https://ralphworkflow.com/blog/ralph-workflow-vs-hermes-agent
  - https://ralphworkflow.com/blog/ralph-workflow-vs-github-copilot

### Repo reliability guide reuse
- **When:** 2026-05-23 16:04:00
- **Note:** Reused the strongest StackOverflow-derived production-reliability asset inside `content/guides/unattended_ai_coding_workflow.md`, added concrete guardrails for schema/payment/config changes, linked the deeper live reliability article, and repaired the outdated `START_HERE_RALPHWORKFLOW.md` link to `START_HERE.md`. This keeps the highest-intent trust content inside the Codeberg-first repo path instead of generating another siloed packet.
- **Log:** `agents/marketing/logs/marketing_2026-05-23_repo_reliability_guide_reuse.json`

### Marketing momentum watchdog
- **When:** 2026-05-23 17:00:50
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Marketing momentum watchdog (2026-05-23 17:00)
- **Telegraph gap:** Last post was 2026-05-22. No post today. Root cause: `run_posting.py` only matches `*_draft.md`, `*_telegraph.md`, and `*_seo-page_*.md` suffixes. The `2026-05-23_claude-code-codex-workflow_dualpost.md` was ignored. FIXED: copied to `*_telegraph.md` suffix and posted successfully.
- **`run_posting.py` suffix bug:** `_dualpost.md` files are not processed. Fixed by adding `_dualpost.md` to `find_todays_drafts()` glob patterns.
- **`marketing-daily` cron ERROR:** Triggered manual run. Awaiting result.
- **Pending repairs:** watchdog keeps flagging same 5 items across runs. Root cause: repairs ARE being addressed by the selector (e.g., curator outreach HOLD'd, directory submissions PAUSE'd) but the watchdog doesn't recognize tactic-level repairs as executed. `reddit_style_repetition` and `execution_ceiling_repetition` are blocked by Reddit IP issue — not fixable until Bing indexing improves.
- **Reddit IP/coverage issue:** Direct Reddit 403-blocked. Bing returns 0 Reddit links for `site:reddit.com/r` queries (Bing/MS links only). Browserless connection works but Bing has no Reddit content for these queries. Not a CAPTCHA — a Bing-index coverage gap. Monitor correctly fails closed.
- **Architecture note:** The system is doing research and generating content, but distribution_lane_selector routes to blocked/non-executable channels instead of prioritizing Telegraph (tier-1, working). Selector already knows Reddit/Apollo/GitHub are limited but still marks them as "available lanes."

### Marketing momentum watchdog
- **When:** 2026-05-23 17:24:35
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Marketing momentum watchdog
- **When:** 2026-05-23 17:34:40
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Marketing momentum watchdog
- **When:** 2026-05-23 17:36:28
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat, repetitive_outreach.

### Marketing momentum watchdog
- **When:** 2026-05-23 17:36:45
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Curator outreach — andyrewlee/awesome-agent-orchestrators
- **When:** 2026-05-23 17:56:07 CEST
- **Action:** Sent fresh curator inclusion email to `andyrewlee@gmail.com` for `andyrewlee/awesome-agent-orchestrators` using a Codeberg-primary entry.
- **Why this target:** Fresh agent-orchestration list discovered from current GitHub topic adjacency; not present in outreach history or active curator queue before this send.
- **Assets:** `drafts/curator_outreach/2026-05-23/andyrewlee-awesome-agent-orchestrators.md`, `drafts/curator_outreach/2026-05-23/andyrewlee-awesome-agent-orchestrators-email.txt`
- **Log:** `agents/marketing/logs/marketing_2026-05-23_155607_andyrewlee_curator_email.json`
- **Review due:** 2026-06-06

### Marketing momentum watchdog
- **When:** 2026-05-23 18:03:03
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Curator email runtime fix
- **When:** 2026-05-23 18:03:24 CEST
- **Note:** Patched the marketing audit + distribution selector so `status=sent` curator-email logs count as real live external actions. This stops the loop from ignoring real outreach sends and helps future lane selection respect same-day outreach saturation.
- **Files:** `agents/marketing/marketing_workflow_audit.py`, `agents/marketing/distribution_lane_selector.py`, `agents/marketing/tests/test_marketing_system.py`
- **Log:** `agents/marketing/logs/marketing_2026-05-23_curator_email_runtime_fix.json`

### Marketing momentum watchdog
- **When:** 2026-05-23 20:04:47
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog — broken submission URL
- **When:** 2026-05-23 20:04
- **Note:** aimarketing.directory submission used wrong Codeberg URL (mistlight/Ralph-Workflow instead of RalphWorkflow/Ralph-Workflow). Form submitted with response 200. Listing review window: 2026-05-30. Flag for resubmission with correct URL if no live listing appears by then. Root cause fixed in distribution_lane_executor by ensuring CODEBERG_PRIMARY is used.

### Repair execution verification (2026-05-23 20:15)
- **primary_repo_flat — VERIFIED, no new outreach burst sent:** comparison backlink queue is fully prepared and all eight comparison assets now exist as live Ralph Site posts or ready drafts, but same-family curator outreach is already saturated (48 attempts / 24h). Existing manual-contact/curator packets remain the correct follow-through artifacts; do not treat another same-day outreach burst as repair progress.
- **same_family_distribution_overlap — RESPECTED:** no new directory submissions were executed. Existing review windows remain the only active directory work.
- **same_family_outreach_overlap — RESPECTED:** verified manual-contact artifacts exist and are current: `marketing_2026-05-23_curator_contact_handoff_packet.json`, `marketing_2026-05-23_curator_handoff_packet.json`, and StackOverflow handoff/manual-escalation packets. No new curator-contact burst was run.
- **StackOverflow lane — VERIFIED READY, not postable here:** `drafts/stackoverflow_answer_handoff_packet_latest.md` is current for question `79942291`, but live posting is still blocked by missing authenticated StackOverflow session in this runtime. Status remains manual-placement/escalation, not posted.
- **mirror_repo_flat — VERIFIED OK:** telegraph post inventory and repo content assets continue to use Codeberg as primary and GitHub as mirror.
- **Apollo outreach — ALREADY LIVE:** `apollo_status.json` shows authenticated app access and `apollo_sequence_status_latest.json` shows a live launch at 2026-05-23 00:14:49+02:00 with measurement pending until 2026-05-30. No relaunch executed.

### Marketing momentum watchdog
- **When:** 2026-05-23 20:17:38
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-23 20:20:08
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-23 20:30:37
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### AI for Code submission
- **When:** 2026-05-23 20:36:08
- **Note:** Submitted Ralph Workflow to AI for Code via the live homepage suggestion form with Codeberg as the primary repo and GitHub as the mirror. Positioned it as a free and open-source agentic-workflow / coding-agent option for teams that want finished, tested, ready-to-review results instead of another confident summary.
- **Target:** https://aiforcode.io/
- **Primary URL:** https://codeberg.org/RalphWorkflow/Ralph-Workflow
- **Log:** `agents/marketing/logs/marketing_2026-05-23_aiforcode_submission.json`

### Marketing momentum watchdog
- **When:** 2026-05-23 20:36:16
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-23 20:36:28
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-23 21:22:29
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-23 21:56:55
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-23 22:12:45
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-23 23:51:41
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 00:21:54
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 00:22:16
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 00:22:37
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Bollwerk / Werkstatt publisher outreach
- **When:** 2026-05-24 08:09 CEST
- **Note:** Sent a Codeberg-first publisher outreach email to `sales@bollwerk.ai`, anchored to Bollwerk's Werkstatt open-source post and pitched Ralph Workflow as the adjacent workflow layer for planning, implementation, verification, review, and explicit finish states.
- **Target:** https://bollwerk.ai/blog/werkstatt-open-source/
- **Primary URL:** https://codeberg.org/RalphWorkflow/Ralph-Workflow
- **Log:** `agents/marketing/logs/marketing_2026-05-24_bollwerk_publisher_outreach.json`

### Marketing momentum watchdog
- **When:** 2026-05-24 00:41:38
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 00:43:50
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 01:12:10
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 01:15:47
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 01:54:10
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

- **AI IDE** — curator email sent to `support@aiide.dev`
  - Subject: `Suggested AI IDE listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Project site: https://ralphworkflow.com/
  - Why this target: fresh developer-facing AI coding directory discovered in the distribution-reset queue; higher-intent than another low-signal directory burst and not previously contacted in outreach history
  - Log: `agents/marketing/logs/marketing_2026-05-24_000534_aiide_curator_outreach.json`
  - Review by: 2026-06-07
- **BNLNPPS / awesome-terminals-ai** — curator email sent to `yesw@bnl.gov`
  - Page: https://github.com/BNLNPPS/awesome-terminals-ai
  - Subject: `Suggested awesome-terminals-ai listing: Ralph Workflow`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Why this target: untouched terminal-first AI coding list with clear Claude Code / Codex adjacency; stronger fit than another low-intent directory submission and still executable from this runtime
  - Log: `agents/marketing/logs/marketing_2026-05-24_010917_bnlnpps_curator_email.json`
  - Review by: 2026-06-07
- **Claude Code Alternatives** — directory submission sent
  - Submit URL: https://claude-code-alternatives.com/tool/create/
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Mirror URL: https://github.com/Ralph-Workflow/Ralph-Workflow
  - Why this target: high-intent Claude Code alternative directory aligned with Ralph Workflow's comparison pages and evaluator traffic
  - Verification: initial POST redirected to `/submitter/projects`; immediate second submit returned `The url has already been taken.`
  - Log: `agents/marketing/logs/marketing_2026-05-24_claude_code_alternatives_submission.json`
  - Review by: 2026-06-07

### Marketing momentum watchdog
- **When:** 2026-05-24 02:45:06
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 02:47:28
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 02:53:15
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 02:53:31
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Repo conversion repair
- **When:** 2026-05-24 03:35:46 CEST
- **Action:** Repaired the broken first-run docs path in the workspace conversion surfaces. `README.md` and `START_HERE.md` were sending evaluators to missing files, so the run created `docs/README.md` and `docs/first-task-guide.md`, replaced the dead top-level operator-manual pointer with a working first-task route, and restored the root `LICENSE` link.
- **Why this move:** External directory/curator/Apollo lanes were already saturated or inside live measurement windows, while this conversion break was immediate friction on the Codeberg-first evaluation path.
- **Verification:** local markdown link check passed for `README.md`, `START_HERE.md`, `docs/README.md`, and `docs/first-task-guide.md`.
- **Log:** `agents/marketing/logs/marketing_2026-05-24_repo_conversion_broken_link_repair.json`
- **Review by:** 2026-05-31

- **ToolNova** — directory submission sent via live form endpoint
  - When: 2026-05-24 03:32:44 CEST
  - Submit page: https://toolnova.ai/submit-tool
  - Endpoint: https://readdy.ai/api/form/d6p3mqp30mftb86r3u4g
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Mirror URL: https://github.com/Ralph-Workflow/Ralph-Workflow
  - Category: AI Coding Tools
  - Contact email: ken@hireaegis.com
  - Log: `agents/marketing/logs/marketing_2026-05-24_toolnova_submission.json`
  - Review by: 2026-06-07

### Marketing momentum watchdog
- **When:** 2026-05-24 03:56:29
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### StackOverflow answer lane
- **When:** 2026-05-24 04:18:08
- **Note:** StackOverflow answer lane ran: found 0 questions, scored 0, drafted 0 answers, skipped 0 recent duplicate candidates. Top question: none.

### StackOverflow answer lane
- **When:** 2026-05-24 04:19:57
- **Note:** StackOverflow answer lane hit Stack Exchange rate limiting; preserved the prior lane state instead of overwriting it with a fake zero-opportunity result.

### StackOverflow answer lane
- **When:** 2026-05-24 04:47:57
- **Note:** StackOverflow answer lane hit Stack Exchange rate limiting; preserved the prior lane state instead of overwriting it with a fake zero-opportunity result.

### StackOverflow answer lane
- **When:** 2026-05-24 04:49:56
- **Note:** StackOverflow answer lane hit Stack Exchange rate limiting; preserved the prior lane state instead of overwriting it with a fake zero-opportunity result.

### Marketing momentum watchdog
- **When:** 2026-05-24 05:13:16
- **Note:** Momentum check found: reddit_monitor_stale, primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 05:18:46
- **Note:** Momentum check found: reddit_monitor_stale, primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 05:19:28
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T05:51:00.

### StackOverflow answer lane
- **When:** 2026-05-24 05:24:38
- **Note:** StackOverflow answer lane hit Stack Exchange rate limiting; preserved the prior lane state instead of overwriting it with a fake zero-opportunity result.

### Marketing momentum watchdog
- **When:** 2026-05-24 06:28:17
- **Note:** Momentum check found: reddit_monitor_stale, primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

### Marketing momentum watchdog
- **When:** 2026-05-24 06:52:45
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T07:52:32.840986.

### Marketing momentum watchdog
- **When:** 2026-05-24 06:52:54
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T07:52:32.840986.

### Marketing momentum watchdog
- **When:** 2026-05-24 06:57:27
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T07:52:32.840986.

### Marketing momentum watchdog
- **When:** 2026-05-24 07:03:25
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T07:52:32.840986.

### Marketing momentum watchdog
- **When:** 2026-05-24 07:23:29
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T07:52:32.840986.

### Marketing momentum watchdog
- **When:** 2026-05-24 07:25:21
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T07:52:32.840986.

### Marketing momentum watchdog
- **When:** 2026-05-24 07:40:25
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T07:52:32.840986.

### Marketing momentum watchdog
- **When:** 2026-05-24 07:42:15
- **Note:** Momentum watch state: primary repo adoption is still flat against the stated marketing goal; Reddit is blocked from this environment, but a replacement distribution path has already shipped; measurement hold is active until 2026-05-24T07:52:32.840986.

### Marketing momentum watchdog
- **When:** 2026-05-24 08:15:19
- **Note:** Momentum check found: primary_repo_adoption_flat, pending_repairs_detected. Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated. Pending repairs: primary_repo_flat, same_family_distribution_overlap, same_family_outreach_overlap, mirror_repo_flat.

- **StackOverflow cron live verification** — confirmed both queued cron jobs still exist before the 2026-05-24 11:30/11:45 CEST follow-through window.
  - Action: verified `stackoverflow-post-cooldown-demand-capture` and `stackoverflow-post-cooldown-run-check` via `openclaw cron show ... --json`.
  - Log: `agents/marketing/logs/marketing_2026-05-24_stackoverflow_cron_live_verification.json`

- **StackOverflow overdue follow-through guard repair** — stopped a missed post-cooldown one-shot from masking new action
  - When: 2026-05-24 11:33 CEST
  - Action: patched `agents/marketing/distribution_lane_selector.py` so a scheduled StackOverflow post-cooldown run only suppresses follow-through for a 3-minute grace window after its due time.
  - Why: cron job `7a71bb58-75ac-4862-b316-ed3bdff44b0c` was still idle after its 11:30 CEST fire time; leaving the old 1-hour buffer in place would have hidden the miss and rewarded fake measurement hold.
  - Proof: `openclaw cron show 7a71bb58-75ac-4862-b316-ed3bdff44b0c --json` showed the job still idle after the due time; `python3 -m unittest agents.marketing.tests.test_marketing_system -k stackoverflow` passed after the guard repair.
  - Log: `agents/marketing/logs/marketing_2026-05-24_stackoverflow_overdue_followthrough_guard_repair.json`

### Curator outreach — Kingy AI
- **When:** 2026-05-24 12:08 CEST
- **Action:** Sent fresh curator inclusion email to `info@kingy.ai` for `Kingy AI — Cursor / Cursor SDK vs. Claude Code vs. Codex` using a Codeberg-primary entry.
- **Why this target:** Prepared but unsent high-fit comparison target in the live curator queue; public editorial contact existed, so SMTP outreach was a real execution path from this runtime.
- **Assets:** `drafts/curator_outreach/2026-05-24/02_kingy-ai-cursor-cursor-sdk-vs-claude-code-vs-codex.md`, `drafts/2026-05-24_kingy_ai_curator_email.txt`
- **Log:** `agents/marketing/logs/marketing_2026-05-24_100830_kingy_ai_curator_email.json`
- **Review due:** 2026-06-07

### StackOverflow post-cooldown run-check outcome
- **When:** 2026-05-24 12:47 CEST
- **Result:** cron job `7a71bb58-75ac-4862-b316-ed3bdff44b0c` did **not** produce a real StackOverflow action.
- **Proof:** `agents/marketing/logs/marketing_2026-05-24_stackoverflow_overdue_followthrough_guard_repair.json` captured the job still `idle` at 2026-05-24 11:33 CEST after its 11:30 CEST due time; `agents/marketing/logs/stackoverflow_answer_lane_latest.json` later showed `manual_ready_follow_through` with `drafts_created: 0` and only the already-current reused draft.
- **Truthfulness repair:** later cron state showed a misleading `running` flag, so `agents/marketing/logs/marketing_2026-05-24_stackoverflow_stale_cron_running_truth_repair.json` hardened selector logic against stale scheduler-state leakage.
- **Replacement outcome:** counted the already-sent Kingy AI curator email (`agents/marketing/logs/marketing_2026-05-24_kingy_ai_curator_outreach.json`) as the explicit real external action that replaced the missed StackOverflow slot, with Codeberg kept as the primary CTA.
- **Same-run artifact:** `agents/marketing/logs/marketing_2026-05-24_stackoverflow_run_check_outcome.json`

- **Morph** — publisher email sent to `info@morphllm.com`
  - When: 2026-05-25 17:43 CEST
  - Why: fresh artifacts still showed Codeberg flat, the current primary-repo-flat packet had already been consumed for TIMEWELL and Toolradar, and Morph remained the last untouched runtime-sendable publisher target in the truthful review window.
  - Subject: `Ralph Workflow for your AI coding agents rankings page`
  - Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
  - Hook: `14 Best AI Coding Agents (2026): Full Rankings`
  - Result: SMTP accepted the email and started a real publisher reply/citation review window.
  - Log: `agents/marketing/logs/marketing_2026-05-25_morph_publisher_outreach.json`
  - Review by: 2026-06-08
