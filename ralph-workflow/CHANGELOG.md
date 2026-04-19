# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Verbose output is now the default.** Ralph surfaces phase banners, plan, analysis/decision state, agent activity, retries, and a final summary by default — output is the product of an agent system. Pass `--quiet` (or `-q`) to opt into the minimal, error-only variant. `--verbosity normal` is still accepted but mapped to verbose so existing wrapper scripts keep working.
- The live dashboard now renders `Plan`, `Analysis`, and `Decision Log` panels backed by `.agent/artifacts/plan.json` and the latest `_analysis_decision` artifacts, not just a prompt preview.
- Phase transitions emitted during a run are both printed above the live region and recorded into the dashboard's decision log.
- Pipelines now end with a `Pipeline Complete` (or `Pipeline Failed`) summary panel that echoes the plan, decision log, metrics, verification status, commit, PR URL, and open risks that the user saw live.

### Added
- `ralph.display.completion_summary` — end-of-run panel renderer reused by the runner for both dashboard and lines modes.
- `ralph.display.panels.analysis` and `ralph.display.panels.decision_log` — new first-class dashboard regions.
- `ralph.display.artifact_reader` — tolerant readers for `plan.json` and `*_analysis_decision.json` used by the dashboard subscriber.
- `ParallelDisplay.emit_phase_transition` / `emit_analysis_result` — helpers that route transitions and decisions through both the live dashboard and the subscriber's decision log.
- `LiveDashboard.print_above` — serialised helper for printing banners above the live region without fighting the render thread.

### Migration
- Users relying on the previously silent default behavior should now pass `--quiet` (or `-q`) explicitly.
- Users currently passing `--verbosity normal` will see verbose output; they will need to switch to `--quiet` to restore the minimal variant.

### Fixed
- `-D` / `--developer-iters` and `-R` / `--reviewer-reviews` CLI flags now correctly control the number of dev/review cycles. Previously, the pipeline ran exactly one dev cycle and one review pass regardless of the flag values. The `developer_iters` setting controls dev cycles; `reviewer_reviews` controls review cycles.
- `-R=0` now skips the review phase entirely instead of running one forced review pass.
- Analysis loopbacks from `development_analysis` no longer consume a development budget slot. Loopbacks are retries of the same iteration, not new iterations.

### Removed
- `max_dev_continuations` config field (previously declared but never used).
- `_legacy_handle_agent_success` reducer path (unreachable in production — policy is always loaded).
- Unused `developer_iters_option` / `reviewer_reviews_option` decorator stubs in `cli/options.py`.

### Migration Notes
- **Users resuming from a checkpoint created by a prior buggy version**: the stored `development_budget_remaining` and `review_budget_remaining` fields will be `0`, causing the fix to silently repeat the old broken behavior. To get the fix, delete the checkpoint before resuming:
  ```bash
  rm .agent/checkpoint.json
  ```
  Then re-run `ralph -D N -R M` from scratch. Affected users already had a broken run (dev ran once), so this is equivalent to restarting the intended work.
