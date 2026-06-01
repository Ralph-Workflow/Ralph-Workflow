# Marketing Control-Loop Truth Repair
Generated: 2026-05-24T22:33:00+02:00

## What changed
- Patched `agents/marketing/marketing_loop_runner.py` so a known `reddit_monitor.py` non-zero exit with `status=search_provider_degraded` is treated as tolerated degraded telemetry instead of an operational bundle failure.
- Patched `agents/marketing/distribution_lane_executor.py` copy generation to remove broken phrases from publisher/contact outreach assets.
- Cleaned the active primary-repo-flat handoff packet copies so the live manual reference no longer contains malformed outreach copy.

## Why this mattered
- The runner was falsely reporting the bundle as unhealthy for a known blocked/degraded Reddit surface, which obscured the real blocker: no measurable Codeberg adoption movement yet.
- The active publisher packet contained broken wording (`Ralph Workflow is ralph workflow is ...`), which directly hurts conversion odds if that packet is used.

## Verification
- Re-ran `python3 agents/marketing/marketing_loop_runner.py`.
- New state: `operational_ok=true`, `certification_ok=false`.
- Independent verification now fails for the real reason only: `Primary repo adoption remains measurement-pending after shipped repairs`.

## Expected outcome improvement
- Cleaner control-loop truth: blocked Reddit telemetry is now a watchpoint, not a fake operational failure.
- Cleaner contact copy: the strongest remaining Codeberg-first publisher asset is now usable without obvious wording defects.

## Still true after repair
- Codeberg remains flat across the recent window.
- Same-family directory/outreach overlap remains paused and measurement-pending.
- The current hold window should not be reset; the scheduled post-hold rerun remains the next truthful execution slot.
