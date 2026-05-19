# Reddit Post Log — 2026-05-19 16:01:56

- Account: `Informal-Salt827`
- Thread URL: https://old.reddit.com/r/ClaudeCode/comments/1taty8a/claude_code_just_shipped_a_run_until_done_mode/
- Comment URL: https://old.reddit.com/r/ClaudeCode/comments/1taty8a/claude_code_just_shipped_a_run_until_done_mode/omo8q4d/
- Note: Autoposted from reddit-monitor shortlist: #1 Claude Code just shipped a "run until done" mode. Upgrade to v2.1.139 for /goal. (`r/ClaudeCode`).
- Report: /home/mistlight/.openclaw/workspace/seo-reports/reddit_monitor_2026-05-19_1520.md
- Rank: 1
- Title: Claude Code just shipped a "run until done" mode. Upgrade to v2.1.139 for /goal.
- Community: `r/ClaudeCode`
- Angle: run-until-done only helps if done is bounded, fail-closed, and easy to review

## Comment body

Honestly the part I'd optimize first is the handoff, not the model stack.

If the run ends with one readable diff, real checks, and a short note about what still looks sketchy, you can move fast without lying to yourself about the result.

Most of the pain is not raw generation. It's stale assumptions, fuzzy ownership, and nobody making the finish easy to review.
