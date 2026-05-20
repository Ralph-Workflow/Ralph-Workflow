# Ralph Workflow vs Conductor OSS

**Last updated:** May 20, 2026 · [Edit this comparison](https://codeberg.org/Ralph-Workflow/Ralph-Workflow) · [← Back to all comparisons](/comparisons)

## At a Glance

| | **Ralph Workflow** | **Conductor OSS** |
|---|---|---|
| **What it is** | Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator. | Enterprise-grade workflow orchestration for AI agents |
| **License** | AGPL (source) / CC0 (outputs) | Free / Open source (Apache 2.0) |
| **Setup** | TOML config files, no cloud required | Varies |
| **Vendor lock-in** | None — own your config | Varies |

## Key Differences

**Ralph Workflow** is a **Conductor OSS** *alternative* for teams that want:

- A simple Ralph-loop core composed into bigger workflow stages
- A strong default workflow for writing software
- Cost control via model routing (cheap models where sufficient)
- A workflow you can use as-is or build on top

**Conductor OSS** is better for:

- Durable execution
- 14+ LLM providers
- MCP support
- Vector DB built-in

## Feature Comparison

| Feature | Ralph Workflow | Conductor OSS |
|---|---|---|
| Multi-agent orchestration | ✅ | ❌ |
| Claude Code integration | ✅ | ❌ |
| OpenCode / Codex integration | ✅ | ❌ |
| Cost model routing | ✅ | ❌ |
| unattended execution | ✅ | ⚠️ |
| Policy-defined config (TOML) | ✅ | ❌ |
| Checkpoint / resume | ✅ | ⚠️ |
| MCP support | ✅ | ✅ |
| Parallel work units | ✅ | ✅ |
| Open source | ✅ | ✅ |
| Self-hosted | ✅ | ⚠️ |

## Why Choose Ralph Workflow Over Conductor OSS

Conductor OSS focuses on Enterprise-grade workflow orchestration for AI agents.
Ralph Workflow's focus is **a simple core loop composed into a stronger software workflow**.

Ralph Workflow's key differentiator is that the core stays simple while the surrounding workflow remains composable,
so teams can use the default path or build their own system on top.

Key advantages:
- **Cost arbitrage**: Route work to cheap models where sufficient, save frontier models for what matters
- **Composable workflow**: planning, development, verification, and follow-up each loop cleanly
- **Policy as code**: Your workflow is a TOML file you diff, version, and audit

## Try Ralph Workflow

```bash
pip install ralph-workflow
cd /path/to/your/project
ralph --init
$EDITOR PROMPT.md  # write your task
ralph  # walk away
```

[Install guide →](https://ralphworkflow.com/docs) · [Quick start →](https://ralphworkflow.com/docs#quick-start) · [Primary Codeberg repo →](https://codeberg.org/RalphWorkflow/Ralph-Workflow) · [GitHub mirror →](https://github.com/Ralph-Workflow/Ralph-Workflow)
