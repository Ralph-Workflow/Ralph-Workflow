# The Missing Step in AI Coding Workflows: Proof, Not Just "Done"

## Trimmed Version (~500 words) — For GitHub Discussions Replies

You give an AI agent a task. It runs. It says "done." You close the laptop. Then you come back and find the code compiles but the logic is wrong, or it solved example A but broke B and C, or it added 300 lines of dead code and removed two critical checks.

The tool said done. The job didn't hold up.

This isn't a tool problem. It's a verification gap — the difference between "the task ran" and "the task was completed correctly." Agents follow scripts. If the script doesn't include verification — and most don't — the "done" signal is worse than useless.

A common pain point: people split work across Claude Code and Codex, or other AI coding tools, and have no clean handoff between them. Sessions get orphaned. Changes conflict. The only handoff mechanism is copy-pasting terminal output. The problem isn't tool choice — it's that no tool provides a reviewable handoff surface between sessions.

The workflow that actually works is boring: sharpen the scope before any agent runs (acceptance criteria, file paths, expected output format), run in isolation, build against constraints, then verify the output against the criteria. If it passes, it's done. If not, it's not — no matter what the agent says.

The real gap lives in finish-state verification. The industry talks about tooling choice endlessly. Nobody talks about what happens after the agent finishes but before you review the work. Without a verification step in the loop, you're trusting the agent's own assessment. And the agent always thinks it did great.

The fix isn't a better agent. It's a workflow that separates execution from verification and requires proof of the latter.