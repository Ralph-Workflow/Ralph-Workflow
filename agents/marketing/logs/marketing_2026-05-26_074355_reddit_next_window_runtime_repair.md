# Reddit next-window runtime repair

- Timestamp: 2026-05-26T07:43:55.659509+02:00
- Status: executed
- Live external action: false

## Why this ran
- Current review window has no truthful do-now packet, but improving the post-hold Reddit lane is still a valid follow-through repair.
- The existing next-window packet had malformed placeholder copy and a dead fallback source path.

## What changed
- Fixed the fallback source path for fresh approved Reddit bodies.
- Sanitized monitor-angle text and stripped mirror-link leakage from packet bodies.
- Added packet-ready normalization so generic placeholder drafts become cleaner thread-specific replies.
- Regenerated the next-window Reddit packet for the current hold release.

## Verification
- `python3 -m unittest agents.marketing.tests.test_reddit_next_window_packet`
- `python3 agents/marketing/reddit_next_window_packet.py`

## Artifacts
- /home/mistlight/.openclaw/workspace/drafts/reddit_next_window_packets_latest.md
- /home/mistlight/.openclaw/workspace/drafts/2026-05-26_reddit_next_window_packets.md
