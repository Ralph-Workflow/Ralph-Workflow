# Reddit Post Log — 2026-05-17 19:28:34

- Account: `Informal-Salt827`
- Thread URL: https://old.reddit.com/r/codex/comments/1tao42q/did_anyone_here_moved_from_claude_to_codex/
- Comment URL: https://old.reddit.com/r/codex/comments/1tao42q/did_anyone_here_moved_from_claude_to_codex/omc9mon/
- Note: Manual post on fresh r/codex migration thread: Claude-to-Codex workflow answer with reviewable-handoff angle.
- Report: live-web-spot-check-2026-05-17T19:25+02:00
- Title: Did anyone here moved from claude to codex recently? And why?
- Community: r/codex
- Angle: switch phase ownership instead of blind brand loyalty; Codex builds, Claude challenges, trust the finish line

## Comment body

I didn’t fully switch so much as change which tool owns which phase.

If the problem is ambiguous or architecture-heavy, I still like Claude first because it forces the shape of the task. If the job is a bounded implementation sprint, Codex is usually the one I trust to push it through faster.

The bigger change for me was moving the trust check to the finish line. Whoever writes the code doesn’t get the final word. I want a small diff, one merged-state test pass, and a short note saying what changed and what still needs judgment. That made “Codex builds, Claude challenges” more useful than trying to pick one permanent winner.

So I didn’t really move from Claude to Codex as much as from “one tool does everything” to “one tool builds, another interrogates the handoff.” If the morning-after re-entry is clean, the stack is working.

That loop is basically why I built RalphWorkflow — free/open source, runs the agents on your own machine, and tries to bring back something reviewable instead of another long transcript.

https://github.com/Ralph-Workflow/Ralph-Workflow
