# Marketing repair — outcome runner reuse bridge

Generated: 2026-05-26T15:35:48

## What broke
- The active loop reuse path only searched `marketing_*.json` logs for prior distribution-architecture repairs.
- The dedicated outcome runner writes the freshest repair truth into `outcome_execution_board_latest.json`, so the next loop could miss that newest state and burn another slot on the same empty-board repair.

## Repair applied
- Added outcome-runner status fallback to `agents/marketing/run.py` for distribution-architecture reuse lookup.
- Added execution-board fingerprint + short-review release metadata to outcome-runner status payloads.
- Backfilled the live `outcome_execution_board_latest.json` artifact so the next cron pass can reuse the latest repair immediately.

## Verification
- Targeted unittest coverage passed for the new fallback and payload metadata.
- Live reuse probe confirms the runner can now resolve a current distribution-architecture repair artifact without forcing another duplicate repair cycle.