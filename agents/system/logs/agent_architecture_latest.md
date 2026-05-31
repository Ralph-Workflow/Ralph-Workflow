# Agent Architecture Watchdog — Latest

- **Checked:** 2026-05-31T12:05:56+02:00
- **Verdict:** watch
- **Architecture-owned gates:** green
- **Independent verification:** qualified_pass

## Live Topology
- 26 jobs, 26 enabled, 0 disabled
- 1 running: agent-architecture-watchdog
- 1 last-error: blocked-channel-recovery (timeout)

## External Blockers
1. **blocked-channel-recovery** — 692 consecutive timeouts, critical escalation
2. **marketing_independent_verification** — artifact stale (64+h), verdict=fail

## Repairs This Run
- Refreshed independent verification → qualified_pass
- Reran architecture verifier → ok, no architecture errors
- External blockers correctly localized as external-owned

## Still Red
- blocked-channel-recovery timeout loop (external domain)
- Marketing primary-repo adoption evidence missing (external owner loop)

## Notes
- Architecture-owned verifier path is coherent; remaining risk is external.
- Whole-stack green is blocked on the two external issues above.
