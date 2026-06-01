# Curator due-follow-up lane repair
Generated: 2026-05-24T12:52:02+02:00

## What changed
- Added selector logic to surface `curator_due_followup` when sent/waiting-review curator targets pass `review_due_date`.
- Added executor support to generate a canonical overdue follow-up packet instead of defaulting to another measurement hold or reset.

## Why this matters
- The current loop had real outreach in flight but no explicit path for overdue follow-through.
- That gap would let genuine next-touch work hide behind fake-still-busy measurement language.

## Verification
- `python3 -m py_compile agents/marketing/distribution_lane_selector.py agents/marketing/distribution_lane_executor.py`
- Safe temporary mutation of `curator_outreach_queue_latest.json` caused `choose_distribution_lane()` to return `curator_due_followup`, then restored the file.
