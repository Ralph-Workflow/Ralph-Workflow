# Ralph Workflow vs Conductor OSS

**Last updated:** May 28, 2026 · [Edit this comparison](https://codeberg.org/Ralph-Workflow/Ralph-Workflow) · [← Back to all comparisons](/comparisons)

## At a Glance

| | **Ralph Workflow** | **Conductor OSS** |
|---|---|---|
| **What it is** | Unattended multi-agent orchestration | Enterprise-grade workflow orchestration for AI agents |
| **License** | AGPL (source) / CC0 (outputs) | Free / Open source (Apache 2.0) |
| **Setup** | TOML config files, no cloud required | Varies |
| **Vendor lock-in** | None — own your config | Varies |

## Key Differences

**Ralph Workflow** is a **Conductor OSS** *alternative* for teams that want:

- Multi-agent orchestration where different phases use different model families
- Cost control via model routing (cheap models where sufficient)
- Policy-defined workflows anyone can read and version in git
- True unattended execution with artifact-based completion criteria

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
| Artifact-based completion | ✅ | ❌ |
| Parallel work units | ✅ | ✅ |
| Open source | ✅ | ✅ |
| Self-hosted | ✅ | ⚠️ |

## Why Choose Ralph Workflow Over Conductor OSS

Conductor OSS focuses on Enterprise-grade workflow orchestration for AI agents.
Ralph Workflow's focus is **multi-agent phase routing with cost arbitrage and policy-defined orchestration**.

Ralph Workflow's key differentiator is the ability to compose multiple agents (Claude, Codex,
OpenCode) into a single unattended pipeline where each phase uses the most cost-effective model.

## Try Ralph Workflow

```bash
pip install ralph-workflow
cd /path/to/your/project
ralph --init
$EDITOR PROMPT.md  # write your task
ralph  # walk away
```

[Install guide →](https://ralphworkflow.com/docs) · [Quick start →](https://ralphworkflow.com/docs#quick-start) · [Primary Codeberg repo →](https://codeberg.org/RalphWorkflow/Ralph-Workflow) · [GitHub mirror →](https://github.com/Ralph-Workflow/Ralph-Workflow)
