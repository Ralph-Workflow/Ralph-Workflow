# Advanced Artifact Configuration

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
That simple core composes into a stronger composable workflow system for substantial, well-specified repo work, and the default workflow is already strong enough to start with before you customize anything.


This page is for operators who want to change the **typed outputs Ralph Workflow expects and records**.
Use it when you need to edit contracts, decision vocabularies, or summary paths without guessing how those outputs connect back to the workflow.

Treat artifacts as operator-facing contracts, not generic notes.
The goal is to keep the workflow reviewable and predictable while the core loop stays simple.

If your question is about workflow routing, use [Advanced Pipeline Configuration](advanced-pipeline-configuration.md). If your question is about MCP servers, use [Advanced MCP Configuration](advanced-mcp-configuration.md).

## Which file am I editing?

- project-local artifact policy → `.agent/artifacts.toml`
- user-global default artifact policy → `~/.config/ralph-workflow-artifacts.toml`
- bundled default / example → `ralph/policy/defaults/artifacts.toml`

In most real repos, start with **`.agent/artifacts.toml`**.

After editing, run:

```bash
ralph --check-policy
ralph --diagnose
```

## What `artifacts.toml` controls

`artifacts.toml` declares the artifact contracts for each drain.

It owns:

- which artifact each drain must submit
- artifact types
- decision vocabularies for analysis artifacts
- summary markdown output paths
- explicit artifact JSON output paths
- which prompt template is responsible for producing that artifact

## The major fields

Each `[artifacts.<name>]` block usually contains:

- `drain`
- `artifact_type`
- `decision_vocabulary`
- `prompt_template`
- `markdown_summary_path`
- `artifact_json_path`

Example:

```toml
[artifacts.development_analysis_decision]
drain = "development_analysis"
artifact_type = "development_analysis_decision"
decision_vocabulary = ["completed", "request_changes", "failed"]
prompt_template = "development_analysis.jinja"
markdown_summary_path = ".agent/DEVELOPMENT_ANALYSIS_DECISION.md"
```

## Decision vocabulary vs routing

This is an important distinction:

- `artifacts.toml` defines the **allowed decision strings**
- `pipeline.toml` defines **where those decisions route**

If you add or rename a decision in `artifacts.toml`, you must update the matching analysis-phase decision routing in `pipeline.toml` too.

## Common advanced user stories

### I want to add a new analysis decision

1. update `decision_vocabulary` in `artifacts.toml`
2. update the matching `[phases.<name>.decisions.*]` routing in `pipeline.toml`
3. run `ralph --check-policy`

### I want human-readable summaries written to different files

Edit `markdown_summary_path`.

### I want a different commit-message artifact path

Edit `artifact_json_path` on the commit artifact block.

### I want to add a new drain artifact

Add a new `[artifacts.<name>]` block and ensure the matching drain/phase expects it.

## What usually goes wrong

- changing decision vocabulary without updating policy routing
- renaming an artifact block without updating the phase/drain that expects it
- treating `artifacts.toml` like generic docs instead of a strict contract file

## Related

- [Configuration Reference](configuration.md)
- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md)
- [Policy Explanation](policy-explanation.md)
