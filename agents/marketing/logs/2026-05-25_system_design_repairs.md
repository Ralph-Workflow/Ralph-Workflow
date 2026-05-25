# System-design repairs executed — 2026-05-25

- Added `agents/marketing/distribution_hunter.py` to force a fresh distribution execution lane selection + artifact write even when the audit is asking for architectural repair.
- Added `agents/marketing/apollo_outbound_verifier.py` and upgraded `apollo_sequence_status.py` so Apollo can no longer look green from list-prep alone; it now distinguishes `launch_ready_unverified_send` from `verified_live_sequence`.
- Fixed `agents/marketing/run.py` to fetch Telegraph views via the Telegraph API instead of treating all posts like write.as pages. This addresses the 0-view disconnect for Telegraph-era content.
- Added `agents/marketing/cron_jobs.json` as a Gateway-owned cron manifest documenting the new execution agents that should run.
