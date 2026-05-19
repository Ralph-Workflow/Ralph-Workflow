# How to Run Your AI Coding Agent Completely Unattended

If you have ever started an AI coding agent on a big task before leaving work, only to find when you got back that it had gone off the rails — looping on the wrong problem, hitting a rate limit silently, or just stopping without finishing — you already know the core problem with most AI coding tools today.

They are great at answering questions. They are bad at finishing work while you are not watching.

That gap is what "unattended coding agent" workflows are starting to solve.

## What Does "Unattended" Actually Mean Here

An unattended coding agent is not just a script that runs in the background. The difference between a background task and an unattended workflow is the presence of checkpoints, recovery paths, and outcome verification.

An unattended run that matters should:

- **Start from a written spec**, not a one-line prompt, so the agent has a clear definition of done before it starts
- **Check its own output** at meaningful phase boundaries, rather than running until token limits are hit
- **Recover from failures** by trying an alternative path instead of just stopping
- **Leave a reviewable artifact** — not just a terminal scrollback, but a diff, a test result, or a structured summary you can actually inspect in the morning

Without those things, you are not running unattended. You are just hoping.

## Why Unattended Coding Is Harder Than It Sounds

The reason most AI coding agents do not work well unattended is that they conflate "done" with "generated tokens." When the model stops producing output, most tools call that done — regardless of whether the code compiles, passes tests, or solves the original problem.

The failure modes are specific:

- **Prompt drift**: the longer a run goes, the further the agent gets from the original intent
- **Silent failures**: rate limits, API timeouts, or tool errors that stop the run without any signal
- **No re-entry point**: if a long run crashes at 90%, there is nothing to pick up from

These are not solved by a longer context window. They are solved by workflow structure.

## What a Real Unattended Workflow Looks Like

A real unattended workflow for AI coding is a loop that goes through distinct phases:

1. **Spec phase** — written task definition before the first line of code
2. **Plan phase** — agent proposes an approach, you approve or correct it
3. **Build phase** — actual implementation with checkpoints
4. **Verify phase** — tests, lints, type checks before calling it done
5. **Review phase** — structured diff + summary for human inspection

If the verify phase fails at any point, the loop goes back to plan rather than continuing with bad output.

Tools like Ralph Workflow implement exactly this structure — running on top of existing CLI agents you already have (Claude Code, Codex CLI, OpenCode), adding the orchestration and checkpoint layers that make overnight runs survivable.

## What You Can Do Tonight

If you want to test whether your current setup can handle a genuinely unattended run, try this:

1. Write a one-paragraph spec for a small but real task (refactor a module, add tests to a legacy file, scaffold a new feature)
2. Run your agent with an explicit "stop if tests fail" gate instead of letting it run to completion
3. Come back and check whether the output is something you would actually merge

If that workflow feels fragile, it is because the missing piece is orchestration — not another prompt.

## The Underlying Tool Question

None of this requires a new AI model or a bigger context window. It requires treating AI coding as a workflow problem instead of a model problem.

The tools that figure that out will be the ones that developers actually leave running overnight.

**Try it on Codeberg (primary repo):** https://codeberg.org/RalphWorkflow/Ralph-Workflow  
GitHub mirror: https://github.com/Ralph-Workflow/Ralph-Workflow
