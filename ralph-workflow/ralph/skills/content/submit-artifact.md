---
name: submit-artifact
description: Use when submitting non-plan Ralph Workflow artifacts (development_result, commit_message, planning_analysis_decision, development_analysis_decision, review_analysis_decision, issues, fix_result, smoke_test_result, commit_cleanup, product_spec) via ralph_submit_artifact, or when a validation error mentions artifact_type and you need the canonical envelope shape
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
`artifact_type` and `content` as either a native JSON object/array or a
JSON-serialized string. It is the right skill for any
artifact type whose format lives under `.agent/artifact-formats/<type>.md`:

- `commit_message`, `commit_cleanup`
- `development_result`
- `issues`, `fix_result`
- `development_analysis_decision`, `planning_analysis_decision`,
  `review_analysis_decision` (and the generic `analysis_decision` alias
  inside an analysis drain)
- `smoke_test_result`, `product_spec`

If you are submitting a plan, this is the wrong skill. Use
`submit-plan-artifact` and the planning tools (`ralph_submit_plan_section`,
`ralph_submit_plan_sections`, `ralph_finalize_plan`) instead.

## Core Flow (canonical submission)

1. Read `.agent/artifact-formats/artifact_formats_index.md` to pick the
   correct `artifact_type` from the supported list.
2. Read `.agent/artifact-formats/<artifact_type>.md` for the type-specific
   required fields, optional fields, and worked examples.
3. Build the inner payload as a plain JSON object (e.g. `{"type": "commit",
   "subject": "..."}` for `commit_message`).
4. Pass the inner payload as `content` either as that native JSON object/array
   or as a JSON-serialized string. Do NOT wrap it in an outer
   `{"type": ..., "content": ...}` envelope — Ralph Workflow adds artifact
   metadata itself.
5. Call `ralph_submit_artifact({"artifact_type": "<type>", "content": {...}})`
   or the equivalent stringified-content envelope.

**Canonical envelope** for `commit_message`:

```json
{
  "artifact_type": "commit_message",
  "content": {"type": "commit", "subject": "type(scope): description"}
}
```

**Canonical proof envelope** for `development_result`:

```json
{
  "artifact_type": "development_result",
  "content": {
    "status": "completed",
    "summary": "Added the foo() regression test, clamped the index in src/foo.py, and verified the focused test passes.",
    "files_changed": "- src/foo.py\n- tests/test_foo.py",
    "plan_items_proven": [
      {
        "plan_item": "Step 1: Add the foo() regression test",
        "proof": "tests/test_foo.py contains test_clamp_handles_out_of_range_index."
      },
      {
        "plan_item": "Step 2: Clamp the foo() index",
        "proof": "src/foo.py clamps the index before lookup while preserving the public foo() signature."
      }
    ],
    "analysis_items_addressed": [
      {
        "analysis_item": "AC-01",
        "proof": "Focused regression test covers the out-of-range index."
      },
      {
        "analysis_item": "AC-02",
        "proof": "Production change prevents the crash and the regression test passes."
      }
    ],
    "verification": [{"command": "pytest tests/test_foo.py -q", "result": "passed"}]
  }
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
   native JSON object or JSON-serialized string and `artifact_type` set to the exact type
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
  "content": {...}}` with no outer wrapper.
- Using `content_path` instead of `content` for agent-facing artifact
  submissions. Use `content` with a native JSON object or JSON-serialized string;
  `content_path` is reserved for non-agent callers.
- Treating a native JSON object as invalid. `content` may be a native JSON
  object/array or a JSON-serialized string; the handler parses both. Keep the
  envelope as `{"artifact_type": "<type>", "content": ...}` either way.
- Inventing an `artifact_type` value. The closed set lives in the index;
  unknown types are rejected with a pointer to the index.
- Using the generic `analysis_decision` alias outside an analysis drain.
  The alias is only valid when the session drain is
  `development_analysis`, `planning_analysis`, or `review_analysis`.

## Red Flags - STOP and Start Over

- "I have read the format doc so I do not need the skill." STOP. The
  skill is a per-artifact-type retry envelope; the format doc is the
  schema. They cover different failure modes.
- "The skill is OPTIONAL therefore ignorable." STOP. The OPTIONAL
  marker means the agent may consult the skill, not that the agent may
  skip the source-of-truth format doc. The skill names the format doc
  explicitly.
- "I will reuse a payload from a prior artifact type." STOP. Every
  artifact type has its own closed enums, required fields, and
  validation rules; copying a payload from one type re-runs every
  validation failure of that type.
- "I will invent an `artifact_type` because the index is missing it."
  STOP. The closed set lives in
  `.agent/artifact-formats/artifact_formats_index.md`; unknown types
  are rejected with a pointer to the index.
- "I will wrap the inner payload in `{"type": ..., "content": ...}`."
  STOP. The canonical envelope is `{"artifact_type": "<type>",
  "content": {...}}` with no outer wrapper.
