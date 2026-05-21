# Agent Architecture Audit

- Checked: 2026-05-21T23:28:12+02:00
- Verdict: **HEALTHY**
- Primary failure mode: **mixed-era marketing certification evidence was the main live architecture risk; it is now fail-closed and freshly re-verified**
- Most urgent fix: **preserve the new coherence gate and keep registry ownership aligned with live Gateway jobs**

## Ownership map

- `ralph-docs-watchdog` → docs quality, remediation, independent docs verification
- `autonomous-marketing-stack` → research, message testing, momentum tracking, marketing independent verification
- `ralph-site-owner-loop` → site rendering, deploy, public SEO surfaces
- `system-health-monitor` → runtime health detection with bounded repair authority
- `blocked-channel-recovery` → generic managed channel recovery
- `research-findings-sync` → research repo sync proof
- `codeberg-github-mirror-sync` → Codeberg→GitHub replication

## Severity-ranked findings

1. **[medium] Marketing certification previously allowed mixed-era artifacts to support a pass decision**
   - Evidence: `agents/marketing/marketing_loop_checker.py`, `agents/marketing/marketing_loop_independent_verify.py`, `agents/marketing/logs/marketing_loop_runner_latest.json`, `agents/marketing/logs/marketing_momentum_watchdog.json`
   - Why it mattered: a newer healthy momentum artifact could coexist with an older runner bundle, weakening evidence coherence.
   - Status: repaired this run.

2. **[low] Architecture ownership boundaries are explicit and currently non-overlapping**
   - Evidence: `agents/system/self_improvement_loops.json`, `agents/system/logs/loop_integrity_latest.json`, `agents/system/logs/health_monitor_latest.json`
   - Why it matters: the registry still matches live scheduler topology and no stray user crontab entries were found.

3. **[low] Learning promotion is real: the watchdog finding became runtime behavior**
   - Evidence: `agents/marketing/marketing_loop_checker.py`, `agents/marketing/marketing_loop_independent_verify.py`, `agents/marketing/logs/marketing_loop_independent_verification.json`
   - Why it matters: the system is turning repeated audit findings into executable gates instead of keeping them as report-only prose.

## Repairs applied this run

- Added a runner-to-peer coherence gate to `agents/marketing/marketing_loop_checker.py`.
- Added the same coherence gate to `agents/marketing/marketing_loop_independent_verify.py`.
- Regenerated the marketing runner bundle and refreshed the marketing verification path.

## Independent verification status

- `agents/system/logs/agent_architecture_independent_verification.json` → pass
- `agents/marketing/logs/marketing_loop_independent_verification.json` → pass
- `agents/system/logs/loop_integrity_latest.json` → all full-contract loops green
- `agents/system/logs/health_monitor_latest.json` → `issues_found=0`

## Ordered fix plan

1. Keep coherence gates on full-contract loops.
2. Keep `self_improvement_loops.json` synchronized with active Gateway jobs.
3. Continue promoting repeated architecture findings into fail-closed runtime checks.

## Highest-risk unresolved issue

- **Owner-only loops still rely on owner-local proof surfaces rather than one normalized registry-wide verifier contract.**
