# Ralph Workflow vs Claude Code

**Last updated:** May 14, 2026 · [Edit this comparison](https://codeberg.org/RalphWorkflow/Ralph-Workflow) · [← Back to all comparisons](/comparisons)

## At a Glance

| | **Ralph Workflow** | **Claude Code** |
|---|---|---|
| **What it is** | Unattended multi-agent orchestration | Anthropic's official CLI for agentic coding |
| **License** | AGPL (source) / CC0 (outputs) | Included with Claude subscription |
| **Setup** | TOML config files, no cloud required | Varies |
| **Vendor lock-in** | None — own your config | Yes |

## Key Differences

**Ralph Workflow** is a **Claude Code** *alternative* for teams that want:

- Multi-agent orchestration where different phases use different model families
- Cost control via model routing (cheap models where sufficient)
- Policy-defined workflows anyone can read and version in git
- True unattended execution with artifact-based completion criteria

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
| Artifact-based completion | ✅ | ❌ |
| Parallel work units | ✅ | ❌ |
| Open source | ✅ | ✅ |
| Self-hosted | ✅ | ⚠️ |

## Why Choose Ralph Workflow Over Claude Code

Claude Code is excellent for interactive, single-agent coding sessions. Ralph Workflow is designed for a
fundamentally different use case: ** unattended multi-phase pipelines** where you define the workflow
once and run it the same way every time.

Key advantages:
- **Multi-phase routing**: Claude plans, a cheap model writes, Claude reviews, a cheap model fixes
- **Cost arbitrage**: Route work to $0.003/1k tokens models where they're sufficient
- **Policy as code**: Your workflow is a TOML file you diff, version, and audit
- **Unattended by design**: Claude Code is interactive-first; Ralph Workflow is built to walk away from

## Try Ralph Workflow

```bash
pip install ralph-workflow
cd /path/to/your/project
ralph --init
$EDITOR PROMPT.md  # write your task
ralph  # walk away
```

[Install guide →](https://ralphworkflow.com/docs) · [Quick start →](https://ralphworkflow.com/docs#quick-start) · [GitHub →](https://github.com/RalphWorkflow/Ralph-Workflow)
