# Freestyle + Ralph Workflow — The Sandboxed Autonomous Coding Pipeline

## The missing piece in the agentic dev stack

Freestyle just launched on HN to 322 points. The YC P26 company builds cloud VMs with copy-on-write fork/resume — purpose-built for running coding agents in isolation. The HN thread lit up with developers asking: how does this compare to Daytona, Modal, E2B? What's the actual workflow?

Here's what nobody in that thread said: sandboxing solves half the problem. You still need something to structure the work.

That's where Ralph Workflow fits.

## Two tools, two problems

Freestyle answers: "Where do I run this agent safely?"
Ralph Workflow answers: "What should the agent actually do, and how do I know when it's done?"

Freestyle gives you an isolated VM. You can fork it, snapshot it, tear it down. Perfect for running untrusted code. But you still need to:

- Decide what task to give the agent
- Break it into phases so it doesn't drift
- Verify the output — not just that it compiled, but that it solves the problem
- Resume if the agent gets cut off mid-session
- Wake up to something reviewable, not a chat log

Ralph Workflow handles that. It's a composable loop framework: plan → build → verify → handoff. Each phase runs in a disciplined loop with explicit boundaries. The agent can't drift into infinite refactoring because the task is bounded. The output is a reviewable diff, not a conversation transcript.

## The pipeline

```text
Freestyle sandbox (isolated VM)
  └── Ralph Workflow (plan-build-verify loop)
        ├── Planning phase: "here's the spec, here's what done looks like"
        ├── Build phase: coding agents execute inside the sandbox
        └── Verify phase: tests run, checks pass, diff is ready to review
```

Run it overnight. Wake up to tested, sandboxed, reviewable output.

## Why local-first matters here

Freestyle is cloud VMs. Ralph Workflow runs on your own machine with the agents you already use (Claude Code, Codex, Gemini CLI). You don't need a separate API key or cloud subscription. You don't need to upload your codebase to someone else's servers.

The combination is pragmatic: Freestyle for the execution environment when you need isolation, Ralph for the orchestration layer that turns "run this agent" into "complete this task."

## Honest assessment

Both tools are young. Freestyle just launched this week. Ralph Workflow is open source and under active development. This isn't a polished enterprise stack — it's two tools that solve real problems in complementary ways.

What makes the combination powerful:

- Freestyle's O(1) VM fork means you can checkpoint mid-build — if the agent goes off the rails, fork back to the last clean state
- Ralph's checkpoint/resume means you don't lose progress when an agent session ends (context windows, rate limits, network drops)
- Together: sandboxed execution with structured task completion, not just sandboxed execution

## Try it tonight

Ralph Workflow is free and open source. Install it, pick one backlog task, and run it:

```bash
pipx install ralph-workflow
ralph --init
# Write a one-paragraph spec for a real task
ralph --run
```

Wake up tomorrow and ask: would I merge this?

⭐ [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow) (primary) · [GitHub](https://github.com/Ralph-Workflow/Ralph-Workflow) (mirror) · [First-task guide](https://ralphworkflow.com/start)
