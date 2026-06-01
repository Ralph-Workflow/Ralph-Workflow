# Self-Repair / Self-Improvement Audit
- Checked: 2026-06-01T02:20:18.920669+02:00
- Loops audited: 25
- Loops with self-repair: 24
- Loops with self-improve: 24
- Loops missing self-repair: 0
- Loops missing self-improve: 1

## Findings (sorted by severity)

### [HIGH] Loop "pypi-auto-unblocker" has NO self-improvement mandate
**Mechanism:** Script UNKNOWN has no self-improvement mandate. When outcomes are flat, this loop will repeat the same tactics forever without improving or redesigning its approach.
**Root cause:** Loop was created without a self-improvement mandate or a third-party verification requirement.
**Recommended fix:** Add a self_improvement_mandate section to the loop script that:
  1. Detects when outcomes are flat for N consecutive runs
  2. Triggers a redesign pass: new agents, prompt rewrites, cron changes, or path retirement
  3. Registers the loop in the self_improvement_loops.json registry with checker/runner/verifier
  4. Requires independent third-party signoff before marking the loop healthy again

