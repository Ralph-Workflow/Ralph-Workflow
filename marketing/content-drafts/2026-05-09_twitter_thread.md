# Twitter Thread: "I let AI code for 4 hours unattended"

**Tweet 1 (hook):**
I ran an AI coding agent for 4 hours straight. Here's what happened: 13 commits, complete feature, zero hands on keyboard after the initial spec.

**Tweet 2 (the setup):**
The secret isn't the agent. It's the spec.
Most devs prompt wrong. They write "build a login page."
You need to write: "Users see a login form with email/password. On submit, validate, show errors inline, redirect to /dashboard on success."

**Tweet 3 (the workflow):**
Ralph Workflow runs a loop:
→ Plan (GPT-4 writes SPEC.md)
→ Develop (Claude Code writes code)
→ Verify (o1 catches logic errors)
→ Commit (only if verification passes)
→ Repeat

Until the spec is done. No babysitting.

**Tweet 4 (the results):**
4 hours.
13 commits.
1 complete feature.
I made coffee, watched TV, and came back to a git log I could actually review.

**Tweet 5 (the edge cases):**
What AI still can't do:
- Understand why a feature matters
- Make tradeoff calls without context
- Handle truly novel problems

What it CAN do:
- Everything else. At 3am. Without complaining.

**Tweet 6 (CTA):**
Ralph Workflow is free CLI.
GitHub: github.com/ghuntley/ralph

The hosted version with team analytics is what I'm building next. Because solo mode works. Team mode is where it gets interesting.
