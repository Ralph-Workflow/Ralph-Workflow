# End-User Stories

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger composable workflow system for substantial, well-specified repo work, and the default workflow is already strong enough to start with before you customize anything.


This page is the plain-English map for real user goals.
Its job is to get you to the right next doc quickly, including overnight use cases and baseline comparison routes.

## I am brand new and want the fastest honest first run

- [Getting Started](getting-started.md)
- [Quickstart](quickstart.md)
- [Choose Your First Ralph Workflow Task](first-task-guide.md)

## I want to know whether my task is even a good fit

- [When Unattended Coding Fits](when-unattended-coding-fits.md)
- [Choose Your First Ralph Workflow Task](first-task-guide.md)

## I already use Claude Code, Codex, OpenCode, or Google Anti Gravity and want a baseline comparison

- [Ralph Workflow vs Claude Code](ralph-workflow-vs-claude-code.md)
- [Ralph Workflow vs Codex CLI](ralph-workflow-vs-codex-cli.md)
- [Ralph Workflow vs OpenCode](ralph-workflow-vs-opencode.md)

## I want to run work overnight without babysitting the terminal

- [When Unattended Coding Fits](when-unattended-coding-fits.md)
- [Run Claude Code Overnight Without Babysitting](run-claude-code-overnight-without-babysitting.md)

## I want to edit `ralph-workflow.toml`

- [Configuration Reference](configuration.md)

Short answer:

- global defaults → `~/.config/ralph-workflow.toml`
- repo-specific override → `.agent/ralph-workflow.toml`
- workflow structure changes → `.agent/pipeline.toml`

## I want to change which agents Ralph Workflow uses

- [Configuration Reference](configuration.md)
- [Which Agent Should I Start With?](which-agent-should-i-start-with.md)

## I want one repo to behave differently from my global defaults

- [Configuration Reference](configuration.md)

Then create a project-local override with:

```bash
ralph --init-local-config
```

## I want to understand what my current workflow policy actually does

- [Policy Explanation](policy-explanation.md)

And run:

```bash
ralph --check-policy
ralph --explain-policy
```

## I want advanced docs for `pipeline.toml`

- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md)

## I want advanced docs for `artifacts.toml`

- [Advanced Artifact Configuration](advanced-artifact-configuration.md)

## I want advanced docs for `mcp.toml`

- [Advanced MCP Configuration](advanced-mcp-configuration.md)

## I want to review whether the result is trustworthy after a run

- [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md)
- [After Your First Ralph Workflow Run](after-your-first-run.md)

## I want to see proof before I install anything

- [What Good Ralph Workflow Output Looks Like](reviewable-output.md)

## I want the command and flag reference

- [CLI Reference](cli.md)

## I am not an end user — I need internals or implementation detail

- [Developer Reference](developer-reference.md)
