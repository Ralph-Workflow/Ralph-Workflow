# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Strict startup validation for custom MCP servers configured via `mcp.toml`. Ralph now completes the standard `initialize` → `notifications/initialized` → `tools/list` handshake against every upstream server before phase 1, and after the upstream check it probes the Claude/Codex/OpenCode wiring it would emit against the same server. Failures abort the pipeline with exit code 1 and surface a redacted error (env var values are never logged). Set `RALPH_MCP_STRICT=0` to fall back to the legacy warn-and-skip behaviour for CI smoke runs.
- `ralph --diagnose` renders a `Custom MCP Servers` table (per-server status with tool counts) and an `Agent Transport Compatibility` table (Claude/Codex/OpenCode reachability per server).

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
