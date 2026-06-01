# Ralph Workflow Marketing Execution Board
Generated: 2026-06-01T04:08:00+02:00

## Why this board exists
- Codeberg is still flat in the active window (9 samples; stars +0, watchers +0, forks +0).
- All external distribution lanes are structurally blocked (no SMTP, no PyPI token, no gh auth, Apollo Cloudflare-blocked, Reddit blocked).
- The highest-value autonomous action is owned-content publishing — blog posts that fill genuine keyword gaps with verifiable product depth.
- This board consolidates one truthful do-now action per run instead of cycling through blocked lanes.

## What was just executed (this run)
- **TOML pipeline configuration blog post** (`content/blog/toml-workflow-configuration-guide.md`) — fills a keyword gap for "TOML workflow configuration", "pipeline.toml", "TOML AI agent configuration" — terms with zero existing dedicated coverage. The post is grounded in real shipped product: the actual pipeline.toml defaults with annotated blocks, agents.toml drain-chain bindings, ralph-workflow.toml main config, parallel fan-out config, checkpoint/resume settings, and mcp.toml tool server setup.

## Keyword gaps still open
After the TOML post publishes, these keyword gaps remain:
1. **"Parallel AI coding agents" / "AI fan-out execution"** — no dedicated post; the composable-pipelines and multi-agent-orchestration posts touch on it but don't deep-dive the parallel coordinator, same-workspace fan-out, structured concurrency, or work-unit isolation
2. **"AI agent checkpoint resume"** — mentioned in comparison tables and the overnight-task guide, but no standalone post on checkpoint save/restore mechanics, pipeline events (`CHECKPOINT_SAVED`), session preservation, and recovery policy
3. **Supplementary: "unattended coding startup script" / "CI pipeline AI coding"** — existing posts touch CI/CD but a concrete startup-script guide would convert readers already in a CI/CD mindset

## Active review windows
- Apollo next review: 2026-05-29T09:00:01.629178+02:00 (currently in launch review window)
- Apollo launch review: 2026-06-05T09:00:01.629178+02:00
- StackOverflow: in cooldown — demand-capture packet already delivered in current review window; do not redeliver

## Distribution lane status
- **All external lanes blocked**: no SMTP, no PyPI token, no gh auth, Apollo Cloudflare-blocked, Reddit blocked
- **StackOverflow**: cooldown active — draft exists but lane cannot be executed from this environment
- **Directory confirmation**: has never produced a Codeberg backlink — do not recommend
- **Manual publisher outreach**: ComputingForGeeks remains uncontacted but requires human handoff (no SMTP)
- **Curator outreach**: 25 targets in active review windows, 5 more in queue — saturated; do not add to queue
- **Telegraph**: guard clear, 0 blogs pending, cron active at 6 AM daily

## Blog publishing status
- **43 posts live** on ralphworkflow.com/blog
- **1 new post written this run**: `toml-workflow-configuration-guide.md` — needs commit + deploy
- **Deploy path**: `git push origin main` → `cap production deploy` (or CI-triggered)
- **Previous post**: `verification-patterns-for-ai-generated-code.md` (published June 1, 44th post)

## PyPI status
- **v0.8.8**: wheel built and twine-check passed, but PyPI token is missing — blocked pending credentials

## Adoption metrics (current)
| Platform | Stars | Watchers | Forks | Open Issues | Downloads/month |
|----------|-------|----------|-------|-------------|-----------------|
| Codeberg | 12 | 2 | 2 | 4 | — |
| GitHub | 1 | 2 | 0 | 0 | — |
| PyPI | — | — | — | — | 1,297 |

## Shared findings reused
- `market_intelligence_latest.json` → adoption metrics, bottleneck diagnosis, four marketing questions
- `distribution_lane_latest.json` → blocker summary, lane status, skip rules
- `marketing_workflow_audit_latest.json` → repair actions, failing tactics, next moves

## Process rule now in force
- One concrete action per run. No cycling through blocked lanes.
- If no external lane is open, write an owned-content post that fills a genuine keyword gap.
- Each blog post must be grounded in real product features, not fabricated comparisons.
- Fix stale artifacts in the same run — a stale execution board for the 3rd time is an escalation event; the fix was writing fresh content keyed to current date and state (this file).
