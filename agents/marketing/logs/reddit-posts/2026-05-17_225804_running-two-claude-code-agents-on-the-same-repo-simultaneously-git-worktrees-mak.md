# Reddit Post Log — 2026-05-17 22:58:04

- Account: `Informal-Salt827`
- Thread URL: https://old.reddit.com/r/ClaudeAI/comments/1t9tolw/running_two_claude_code_agents_on_the_same_repo/
- Comment URL: https://old.reddit.com/r/ClaudeAI/comments/1t9tolw/running_two_claude_code_agents_on_the_same_repo/omddojv/
- Note: Manual post on worktrees thread: semantic invalidation + shared-boundary owner angle with GitHub link.
- Report: /home/mistlight/.openclaw/workspace/seo-reports/reddit_monitor_2026-05-17_2115.md
- Rank: 5
- Title: Running two Claude Code agents on the same repo simultaneously. Git worktrees make it work.
- Community: r/ClaudeAI
- Angle: worktrees solve collisions, not semantic invalidation or merge-time review

## Comment body

Git worktrees definitely solve the collision problem, but the thing that bites me later is semantic invalidation.

One agent changes a schema, config, or shared assumption, another branch still looks “clean” in isolation, and then the pain shows up at merge time instead of during execution.

What helped me more than adding more sessions:
- one explicit owner for shared boundaries like schema, auth, config, and migrations
- every other agent leaves notes or diffs there instead of silently editing the same surface
- replay each branch on current main before merge and run one merged-state check
- end each run with a short finish receipt: touched boundaries, checks run, assumptions made, and what still needs human judgment

That keeps worktrees in the useful zone: great for isolation, not a false promise that the overall result is safe.

If you're doing repo-scale work overnight, that's the gap Ralph Workflow is built for: it's free and open source, runs the agent CLIs you already use on your own machine, and is for developers who want to walk away from work that's too big to babysit but too risky to trust blindly. The point is waking up to something reviewable instead of a pile of parallel sessions all saying "done."

https://github.com/Ralph-Workflow/Ralph-Workflow
