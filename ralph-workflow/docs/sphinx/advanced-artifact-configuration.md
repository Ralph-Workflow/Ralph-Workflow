# Advanced Artifact Configuration

This page is for operators who want to change the **typed outputs Ralph Workflow expects and records**.
Use it when you need to edit contracts, decision vocabularies, or summary paths without guessing how those outputs connect back to the workflow.
The core loop stays simple, but the artifact layer is where you make the workflow reviewable and predictable for your team.

Treat artifacts as operator-facing contracts, not generic notes.
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

## Weak-model-compatible JSON schema conventions

Artifact schemas submitted to the MCP artifact submission flow should
be designed to stay well-formed under weaker model outputs. The rules
below are the bundled convention; violations are caught at submit time
and surface as a `PolicyValidationError`.

1. **Maximum nesting depth: 3 levels.** No deeper. If you need more
   depth, flatten the structure.
2. **No `$ref`.** All types must be inlined. Weak models lose context
   when navigating reference chains.
3. **No `oneOf`.** Use `anyOf` with flat discriminated objects instead.
4. **Enum policy: maximum 7 values.** Each enum value must also be
   listed in the property's `description` field. When more than 7
   values are needed, split into multiple properties or use a category
   + subcategory pattern.
5. **Explicit `required` arrays.** Every object must have an explicit
   `required` array listing ALL mandatory fields. Never rely on
   defaults or implicit optionality.
6. **`anyOf` pattern for discriminated unions.** When a property can
   be one of several shapes, use `anyOf` with flat discriminated
   objects. Each variant must be a complete, self-contained object
   with a `type` property with a `const` value as the discriminator
   and its own `required` array.
7. **No `additionalProperties: true`.** Always set
   `additionalProperties: false` on objects. This prevents models from
   inventing fields and makes validation errors specific.
8. **String content for rich text.** Use `"type": "string"` for any
   content that was previously mixed XML content.
9. **Array items must be specified.** Every array must have an explicit
   `items` schema. Use `minItems` and `maxItems` where there are
   known bounds.
10. **Description on every property.** Every property MUST have a
    `description` field that explains what the field represents, what
    values are valid, and any constraints.

## Related

- [Configuration Reference](configuration.md)
- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md)
- [Concepts → Artifact lifecycle](concepts.md#artifact-lifecycle)
