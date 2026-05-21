# Agent Architecture Audit

- Checked: 2026-05-21T06:58:56.893980+02:00
- Verdict: **DEGRADED**
- Primary failure mode: **the runtime is cleaner now, but blocked-channel recovery and loop-verifier contracts are still only partially normalized**
- Most urgent fix: **give blocked-channel recovery one explicit runtime contract: one owner schedule, or separate entrypoints with separate verifier evidence**

## Severity-ranked findings

1. **[medium] Blocked-channel recovery still has two schedules driving one code path**
   - Why it matters: `blocked-channel-deep-review` and `blocked-channel-followup` are now clearly unblocker-owned, but both still run the same unblocker path, so cadence overlap remains more implicit than enforced.
   - Evidence: `/home/mistlight/.openclaw/cron/jobs.json:272`, `/home/mistlight/.openclaw/cron/jobs.json:412`, `/home/mistlight/.openclaw/workspace/agents/unblocker/run.py:1`, `/home/mistlight/.openclaw/workspace/agents/system/self_improvement_loops.json:90`.

2. **[medium] Most live repair/watchdog loops are still metadata-tracked rather than full checker/runner/verifier contracts**
   - Why it matters: the registry now reflects the real topology, but most non-docs loops are still `tracked_only`, so the stronger loop-governance rule is not yet uniformly runtime-enforced.
   - Evidence: `/home/mistlight/.openclaw/workspace/AGENTS.md:170`, `/home/mistlight/.openclaw/workspace/agents/system/self_improvement_loops.json:27`, `:78`, `:108`, and `/home/mistlight/.openclaw/workspace/agents/system/logs/loop_integrity_latest.json`.

3. **[low] OS-cron drift was repaired, but there is no explicit allowlist guard yet**
   - Why it matters: this run removed hidden legacy scheduler entries, but a future stray user-crontab job could still return unless an audit guard fails hard on unexpected entries.
   - Evidence: `/home/mistlight/.openclaw/workspace/agents/system/loop_integrity_audit.py:28`, `crontab -l` checked on **May 21, 2026**, and `/home/mistlight/.openclaw/workspace/agents/system/logs/agent_architecture_independent_verification.json`.

## Repairs applied this run

- Kept the system health monitor inside runtime/cron scope; no cross-owner marketing repair path remains.
- Renamed and clarified the unblocker cron ownership so the live jobs are `blocked-channel-deep-review` and `blocked-channel-followup`, not Reddit-branded generic recovery.
- Rewrote the disabled docs aggressive self-heal job into explicit legacy/deactivated language.
- Patched `loop_integrity_audit.py` so `REMOVED:` docs OS-cron placeholders are never restored, and expanded `self_improvement_loops.json` to track the live repair/watchdog topology.
- Removed legacy HireAegis/content/community/product OS-cron entries.
- Migrated Codeberg→GitHub mirror sync into Gateway cron as `codeberg-github-mirror-sync` and removed the old OS-cron sync entry.
- Replaced the unblocker AGENTS prompt contamination with proof/handoff rules that allow explicit human-required blockers when genuinely necessary.

## Independent verification status

- **Performed**
- Artifact: `/home/mistlight/.openclaw/workspace/agents/system/logs/agent_architecture_independent_verification.json`
- Result: **pass** at **2026-05-21T04:57:16Z** after the mirror-sync migration and crontab cleanup.

## Ordered fix plan

1. Normalize blocked-channel recovery into one explicit runtime contract.
2. Upgrade non-docs self-improvement loops to full contracts, or explicitly classify them as non-self-improving/monitor-only in schema and audits.
3. Add an OS-crontab allowlist invariant so hidden scheduler layers fail audits immediately.

## Highest-risk unresolved issue

- **Shared blocked-channel code path with split schedules but no split verifier contract.**
