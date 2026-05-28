# RFC: Reddit Channel Repair — 2026-05-25

## Problem

The **sole active distribution channel (Reddit) is completely blocked**:
- Server IP gets HTTP 403 on all Reddit login attempts (both new and old Reddit)
- PRAW OAuth credentials in TOOLS.md are placeholders
- Last Reddit post: 131 hours ago (May 20 or so)
- Reddit monitor runs but has nothing to distribute

## Status

- Watchdog status: `watch`
- Watch actions flagged: `reddit_channel_blocked`, `primary_repo_adoption_flat`
- Pending repairs: **empty** ← this is the problem; no repair is queued

## Root Causes

1. **IP block**: Reddit explicitly blocks this server's IP range. No browser automation or PRAW will work from this machine.
2. **Missing credentials**: Even if IP weren't blocked, PRAW needs real `client_id`/`client_secret` from a Reddit app registration.
3. **No fallback channel**: All Reddit-dependent marketing flow has zero redundancy.

## Required Decisions (human needed)

1. **Credentials**: Should we register a real Reddit OAuth app and fill in TOOLS.md PRAW fields?
2. **IP work-around**: Options:
   - Set up a remote runner/VPS that isn't IP-blocked
   - Use a proxy/VPN service
   - Route Reddit posting through a third-party scheduling tool (Later, Buffer, etc.) that has its own IP
3. **Alternative channels**: Should we diversify away from Reddit entirely for some campaigns?
   - Apollo.io outreach (already logged in)
   - Direct to niche forums/communities
   - Email sequences

## Immediate Action Needed

The marketing momentum watchdog cannot fix this alone. This requires either:
- A new agent/runner on an unblocked IP, or
- A decision to re-architect away from Reddit as primary channel
