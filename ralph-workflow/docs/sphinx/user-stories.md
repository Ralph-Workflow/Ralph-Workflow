# End-User Stories

This page is the plain-English route map for real user goals.
Use it when you know what you are trying to do but do not care which doc family contains the answer.
Each section points at the shortest next page for that job, including first-run, overnight use, configuration, comparison, proof, and internals.

## I am brand new and want the fastest honest first run

- [Getting Started](getting-started.md)
- [Quickstart](quickstart.md)
- [Choose Your First Ralph Workflow Task](first-task-guide.md)

## I want to know whether my task is even a good fit

- [When Unattended Coding Fits](when-unattended-coding-fits.md)
- [Choose Your First Ralph Workflow Task](first-task-guide.md)

## I already use Claude Code, Codex, or OpenCode and want a baseline comparison

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

## I want to change which agents Ralph uses

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

- [What Good Ralph Workflow Output Looks Like](../../../docs/reviewable-output.md)

## I want the command and flag reference

- [CLI Reference](cli.md)

## I am not an end user — I need internals or implementation detail

- [Developer Reference](developer-reference.md)
