---
name: submit-artifact
description: Use when submitting any Ralph Workflow artifact (plan, development_result, commit_message, planning_analysis_decision, development_analysis_decision, review_analysis_decision, issues, fix_result, smoke_test_result, commit_cleanup, product_spec) via ralph_submit_artifact
---

# submit-artifact

## Overview

This is an **OPTIONAL** skill that lives alongside the canonical artifact
format docs under `.agent/artifact-formats/`. Use it as a quick lookup
before submitting any artifact, not as a substitute for the per-type format
doc. The format docs and the index are the source of truth.

**Skill name vs MCP tool name.** This skill is named `submit-artifact`. It
shares a prefix with the MCP tool `ralph_submit_artifact`, but they are
different identifiers: the skill is a passive reference document; the MCP
tool is the active submission entry point. Always invoke the MCP tool by its
full name `ralph_submit_artifact`.

## When to Use

Use this skill when you are about to call `ralph_submit_artifact` with an
`artifact_type` and a `content` JSON string. It is the right skill for any
artifact type whose format lives under `.agent/artifact-formats/<type>.md`:

- `commit_message`, `commit_cleanup`
- `development_result`
- `issues`, `fix_result`
- `development_analysis_decision`, `planning_analysis_decision`,
  `review_analysis_decision` (and the generic `analysis_decision` alias
  inside an analysis drain)
- `smoke_test_result`, `product_spec`
- `plan` (the atomic short-plan path; for staged step-wise submission see
  the companion `submit-plan-artifact` skill instead)

If you are about to call `ralph_submit_plan_section` or `ralph_finalize_plan`
for a non-trivial plan, this is the wrong skill — use
`submit-plan-artifact` instead.

## Core Flow (one-shot)

1. Read `.agent/artifact-formats/artifact_formats_index.md` to pick the
   correct `artifact_type` from the supported list.
2. Read `.agent/artifact-formats/<artifact_type>.md` for the type-specific
   required fields, optional fields, and worked examples.
3. Build the inner payload as a plain JSON object (e.g. `{"type": "commit",
   "subject": "..."}` for `commit_message`).
4. Stringify the inner payload into a JSON string and pass it as the
   `content` field. Do NOT wrap it in an outer `{"type": ..., "content": ...}`
   envelope — Ralph Workflow adds artifact metadata itself.
5. Call `ralph_submit_artifact({"artifact_type": "<type>", "content":
   "<fresh JSON string>"})`.

**Minimal one-shot envelope** for `commit_message`:

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\":\"commit\",\"subject\":\"type(scope): description\"}"
}
```

**Minimal one-shot envelope** for `development_result`:

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\":\"completed\",\"summary\":\"Implemented the feature.\",\"files_changed\":\"- src/main.py\",\"plan_items_proven\":[{\"plan_item\":\"Step 1: Add feature\",\"proof\":\"Added the feature and tests.\"}]}"
}
```

## Recovery from a Bad Payload

When `ralph_submit_artifact` rejects a payload, the helpers
`_raise_format_doc_error` and `_raise_index_format_error` raise an
`InvalidParamsError` whose message points at the relevant format doc (or the
index) and names `ralph_submit_artifact` as the retry tool. Read the message,
then:

1. If the message mentions `artifact_formats_index.md`, your `artifact_type`
   is missing or unknown. Re-check `.agent/artifact-formats/artifact_formats_index.md`
   for the exact spelling.
2. If the message mentions `<artifact_type>.md`, your inner payload failed
   type-specific validation. Re-read `.agent/artifact-formats/<artifact_type>.md`
   for the required fields and retry.
3. If the helper `_artifact_content_format_error` is raised, your payload
   is missing the `content` field. Re-issue the call with `content` set to a
   freshly generated JSON string and `artifact_type` set to the exact type
   name.

**Worked retry envelope** for a `_raise_format_doc_error` style failure on
`development_result`:

```json
{
  "artifact_type": "development_result",
  "content": "{...valid development_result JSON...}"
}
```

**Worked retry envelope** for a `_raise_index_format_error` style failure
(unknown `artifact_type`):

```json
{
  "artifact_type": "<exact-type-name-from-index>",
  "content": "{...valid <type> JSON...}"
}
```

## Source of Truth Reference

- `.agent/artifact-formats/artifact_formats_index.md` — the index that lists
  every supported `artifact_type` and points to its format doc. Start here
  when choosing a type.
- `.agent/artifact-formats/<artifact_type>.md` — the per-type format doc
  with required fields, optional fields, and worked examples. Read this
  before crafting the inner payload.

If this skill and a format doc ever disagree, the format doc wins.

## Common Mistakes

- Treating this skill as authoritative. The format doc at
  `.agent/artifact-formats/<artifact_type>.md` is the source of truth; this
  skill is a quick pointer, not a substitute.
- Conflating `submit-artifact` (this skill) with the MCP tool
  `ralph_submit_artifact`. The skill is the passive reference; the MCP tool
  is the active submission entry point.
- Wrapping the inner payload in an outer `{"type": ..., "content": ...}`
  envelope. The canonical envelope is `{"artifact_type": "<type>",
  "content": "<fresh JSON string>"}` with no outer wrapper.
- Using `content_path` instead of `content` for agent-facing artifact
  submissions. Use `content` with a freshly generated JSON string;
  `content_path` is reserved for non-agent callers.
- Passing an object instead of a JSON string for `content`. The `content`
  field must be a stringified JSON object, not the object itself.
- Inventing an `artifact_type` value. The closed set lives in the index;
  unknown types are rejected with a pointer to the index.
- Using the generic `analysis_decision` alias outside an analysis drain.
  The alias is only valid when the session drain is
  `development_analysis`, `planning_analysis`, or `review_analysis`.