# Self-Repair / Self-Improvement Audit
- Checked: 2026-06-04T21:38:53.063852+02:00
- Loops audited: 21
- Loops with self-repair: 19
- Loops with self-improve: 19
- Loops missing self-repair: 0
- Loops missing self-improve: 2

## Findings (sorted by severity)

### [HIGH] Loop "pypi-auto-unblocker" has NO self-improvement mandate
**Mechanism:** Script UNKNOWN has no self-improvement mandate. When outcomes are flat, this loop will repeat the same tactics forever without improving or redesigning its approach.
**Root cause:** Loop was created without a self-improvement mandate or a third-party verification requirement.
**Recommended fix:** Add a self_improvement_mandate section to the loop script that:
  1. Detects when outcomes are flat for N consecutive runs
  2. Triggers a redesign pass: new agents, prompt rewrites, cron changes, or path retirement
  3. Registers the loop in the self_improvement_loops.json registry with checker/runner/verifier
  4. Requires independent third-party signoff before marking the loop healthy again

### [HIGH] Loop "internal-linking-watchdog" has NO self-improvement mandate
**Mechanism:** Script UNKNOWN has no self-improvement mandate. When outcomes are flat, this loop will repeat the same tactics forever without improving or redesigning its approach.
**Root cause:** Loop was created without a self-improvement mandate or a third-party verification requirement.
**Recommended fix:** Add a self_improvement_mandate section to the loop script that:
  1. Detects when outcomes are flat for N consecutive runs
  2. Triggers a redesign pass: new agents, prompt rewrites, cron changes, or path retirement
  3. Registers the loop in the self_improvement_loops.json registry with checker/runner/verifier
  4. Requires independent third-party signoff before marking the loop healthy again

