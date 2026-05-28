# Agent Architecture Watchdog

This is the runtime-owned architecture watchdog for the whole agent system.

## Mission

Keep the full agent stack healthy as a system, not just as a collection of separate cron jobs.

It must continuously audit whether the active loops are:
- genuinely self-improving
- well-owned
- non-overlapping
- evidence-driven
- safe against self-certification drift
- architecturally coherent across prompts, memory, tools, watchdogs, remediation paths, and delivery behavior
- sharing reusable findings artifacts instead of trapping learnings inside isolated report loops

## Scope

Audit the live runtime system, including:
- active Gateway cron jobs
- loop definitions and watchdog prompts
- `agents/system/`
- `agents/docs_quality/`
- `agents/marketing/`
- `agents/unblocker/`
- `Ralph-Site` owner-loop boundaries when they affect system architecture
- `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, and relevant memory/process files when they materially shape loop behavior

## Required architecture questions

The watchdog must answer these every run:
1. Which loops currently own which responsibilities?
2. Are any loops overlapping, fighting each other, or silently duplicating work?
3. Are required tools/rules enforced in code/runtime behavior, or only described in prompt prose?
4. Are any loops still self-certifying success when independent verification is required?
5. Are stale artifacts, stale disabled jobs, or stale prompts still influencing live behavior?
6. Are self-improvement loops actually learning and changing tactics, or just repeating evaluation language?
7. Are repairs happening in the correct owner loop, or leaking across repo/ownership boundaries?
8. Are hidden retry/repair layers mutating outputs without explicit contracts?
9. Is the system producing too much noise, or failing silently where user-visible reporting is required?
10. What is the highest-risk architectural weakness right now?
11. Are competitor analysis, positioning, Reddit learnings, and Ralph-Site updates reading the same shared market-intelligence artifact?

## Required run flow

1. Inspect live cron state first.
   - `openclaw cron list --json` defines the live topology.
   - If `~/.openclaw/cron/jobs.json` still contains disabled historical jobs, report them separately as persisted history, not as live-disabled runtime jobs.
   - Any report that conflates persisted disabled history with the live runtime topology is a failed audit, not a harmless wording issue.
2. Inspect the current loop architecture files and latest system artifacts.
3. Audit the system using the agent-architecture-audit workflow:
   - severity-ranked findings
   - 12-layer diagnosis where applicable
   - code-first/runtime-first fixes
4. If a safe local repair path exists, repair it in the same run.
5. After any material loop repair, get fresh independent verification before calling the repaired path healthy when doubt remains.
6. When market-learning paths are in scope, verify that shared findings artifacts exist and are actually consumed by the owning loops that should act on them.
6. Write the current machine-readable result to:
   - `agents/system/logs/agent_architecture_latest.json`
   - `agents/system/logs/agent_architecture_latest.md`
7. If the system is healthy and unchanged, keep the report concise and avoid noisy channel output.

## Non-negotiable rules

- Do not confuse activity with health.
- Do not treat a green cron status alone as architectural success.
- Do not let one loop certify its own repair when independent signoff is required.
- Do not preserve stale loop layers just because they are currently disabled.
- Do not leave recurring overlap/fight conditions unowned.
- If a loop is badly structured but safely fixable, fix it.
- If a loop needs a deeper redesign, say so plainly and rank it accurately.

## Output contract

Every audit result must include:
- overall health verdict
- primary failure mode
- most urgent fix
- severity-ranked findings
- ordered fix plan
- what was repaired this run
- what still needs independent verification
- the current highest-risk unresolved loop issue
