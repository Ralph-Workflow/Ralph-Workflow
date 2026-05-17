# Reddit Post Log — 2026-05-17 18:28:28

- Account: `Informal-Salt827`
- Thread URL: https://old.reddit.com/r/ClaudeCode/comments/1tf720n/using_claude_with_codex_anyone_else/
- Comment URL: https://old.reddit.com/r/ClaudeCode/comments/1tf720n/using_claude_with_codex_anyone_else/ombxdri/
- Note: Manual post after autopost parser fix: Using Claude with Codex, anyone else? (r/ClaudeCode).
- Report: /home/mistlight/.openclaw/workspace/seo-reports/reddit_monitor_2026-05-17_1815.md
- Rank: 1
- Title: Using Claude with Codex, anyone else?
- Community: r/ClaudeCode
- Angle: one tool pushes, one checks, and the run is only useful when the finish is easy to review

## Comment body

The cleanest split I've found is boring on purpose: one tool writes, the other challenges.

Have the builder make the change, then have the second pass look for gaps in tests, edge cases, or assumptions. After that, judge the run on the diff + checks rather than on either tool's self-report.

That usually works better than trying to make both tools do everything at once.

If the useful part here is "one tool builds, one checks, then judge the result like a PR," RalphWorkflow is my free/open-source take on that loop. It keeps the agents on your own machine and pushes toward reviewable output rather than another long transcript.

https://github.com/Ralph-Workflow/Ralph-Workflow
