# Agent Architecture Audit

- Checked: 2026-05-21T04:49:13.764280+02:00
- Verdict: **DEGRADED**
- Primary failure mode: **ownership leakage and duplicate recovery layers can hide behind green cron state**
- Most urgent fix: **consolidate the unblocker path so one clearly-owned loop handles blocked-channel recovery**

## Severity-ranked findings

1. **[high] Repaired this run — system-health monitor had a hidden cross-owner repair path into marketing**
   - Why it matters: the system repair loop was able to inspect marketing-state artifacts and trigger marketing-active-loop itself, which violated owner boundaries and created a hidden second-pass mutation path.
   - Evidence: `agents/system/health_monitor.py` was narrowed; `python3 agents/system/health_monitor.py` now returns `✅ System health OK - no issues detected`; independent verification passed in `agents/system/logs/health_monitor_independent_verification.json`.

2. **[high] Live unblocker overlap remains: `blocked-channel-review` and `reddit-autopost-followup` both drive `agents/unblocker/run.py`**
   - Why it matters: one job is generically named and one is Reddit-branded, but both run the same blocked-channel recovery path, so ownership is blurred and duplicate recovery/noise risk remains.
   - Evidence: both enabled cron jobs in `~/.openclaw/cron/jobs.json` point to the unblocker unittest + `agents/unblocker/run.py`; `agents/unblocker/BLOCKED_CHANNELS.json` covers non-Reddit channels too.

3. **[medium] Disabled docs watchdog still carries a live-sounding permanent prompt**
   - Why it matters: `docs-stack-aggressive-10min-self-heal` is disabled, but its payload still calls itself the permanent docs watchdog even though the independent verifier-supervisor is the active authority.
   - Evidence: disabled job payload in `~/.openclaw/cron/jobs.json`; healthy current state in `agents/docs_quality/docs_stack_temp_watchdog_status.json` and active independent verifier job `ralph-workflow-docs-verifier-supervisor`.

4. **[medium] The self-improvement registry is incomplete relative to the live cron topology**
   - Why it matters: `agents/system/self_improvement_loops.json` only registers docs, marketing, and Ralph-Site, while live repair/watchdog loops such as `system-health-monitor`, `blocked-channel-review`, and `agent-architecture-watchdog` remain outside the declared registry/verifier map.
   - Evidence: `agents/system/self_improvement_loops.json` vs live cron inventory in `~/.openclaw/cron/jobs.json`.

## Repairs applied this run

- Patched `agents/system/health_monitor.py` to remove marketing artifact inspection and cross-owner triggering.
- Verified the repaired script locally:
  - Command: `python3 /home/mistlight/.openclaw/workspace/agents/system/health_monitor.py`
  - Result: `✅ System health OK - no issues detected`

## Independent verification status

- **Performed**
- Artifact: `agents/system/logs/health_monitor_independent_verification.json`
- Result: pass — independent check confirmed the repaired health monitor no longer reads marketing audit/adoption/sync artifacts and still executes cleanly.

## Ordered fix plan

1. Consolidate or split the unblocker schedules so the generic blocked-channel loop has one owner and the Reddit name is only used for genuinely Reddit-specific work.
2. Normalize or retire the disabled docs aggressive self-heal payload so stale prompt language cannot contaminate future audits/reactivation.
3. Expand `agents/system/self_improvement_loops.json` into a complete registry for live repair/watchdog loops with explicit owner + verifier contracts.

## Highest-risk unresolved issue

- **Duplicate unblocker scheduling with misleading Reddit ownership.**
