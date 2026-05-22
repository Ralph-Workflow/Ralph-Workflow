# Ralph Workflow vs Claude Code

**Last updated:** May 22, 2026 · [Edit this comparison](https://codeberg.org/Ralph-Workflow/Ralph-Workflow) · [← Back to all comparisons](/comparisons)

## At a Glance

| | **Ralph Workflow** | **Claude Code** |
|---|---|---|
| **What it is** | Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator. | Anthropic's official CLI for agentic coding |
| **License** | AGPL (source) / CC0 (outputs) | Included with Claude subscription |
| **Setup** | TOML config files, no cloud required | Varies |
| **Vendor lock-in** | None — own your config | Yes |

## Key Differences

**Ralph Workflow** is a **Claude Code** *alternative* for teams that want:

- A simple Ralph-loop core composed into bigger workflow stages
- A strong default workflow for writing software
- Cost control via model routing (cheap models where sufficient)
- A workflow you can use as-is or build on top

**Claude Code** is better for:

- Anthropic's official CLI
- Deep Claude integration
- File editing
- Tool use

## Feature Comparison

| Feature | Ralph Workflow | Claude Code |
|---|---|---|
| Multi-agent orchestration | ✅ | ⚠️ |
| Claude Code integration | ✅ | ✅ |
| OpenCode / Codex integration | ✅ | ❌ |
| Cost model routing | ✅ | ❌ |
| unattended execution | ✅ | ⚠️ |
| Policy-defined config (TOML) | ✅ | ❌ |
| Checkpoint / resume | ✅ | ⚠️ |
| MCP support | ✅ | ⚠️ |
| Parallel work units | ✅ | ❌ |
| Open source | ✅ | ✅ |
| Self-hosted | ✅ | ⚠️ |

## Why Choose Ralph Workflow Over Claude Code

Claude Code is excellent for interactive, single-agent coding sessions. Ralph Workflow is built for a
different use case: a composable workflow system with a simple core and a stronger default path for bigger software work.

Key advantages:
- **Composable workflow**: planning, development, verification, and follow-up can each loop cleanly
- **Cost arbitrage**: Route work to $0.003/1k tokens models where they're sufficient
- **Policy as code**: Your workflow is a TOML file you diff, version, and audit
- **Default + extensible**: use the shipped workflow first, then extend it without replacing the core

## Try Ralph Workflow

```bash
pip install ralph-workflow
cd /path/to/your/project
ralph --init
$EDITOR PROMPT.md  # write your task
ralph  # walk away
```

[Install guide →](https://ralphworkflow.com/docs) · [Quick start →](https://ralphworkflow.com/docs#quick-start) · [Primary Codeberg repo →](https://codeberg.org/RalphWorkflow/Ralph-Workflow) · [GitHub mirror →](https://github.com/Ralph-Workflow/Ralph-Workflow)
