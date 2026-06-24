---
name: submit-development-result-artifact
description: Use when submitting a development_result artifact with plan_items_proven and analysis_items_addressed proof entries via ralph_submit_artifact, or when plan_items_proven or analysis_items_addressed were rejected as unknown references and you need to recover the canonical-reference matching rule
---

# submit-development-result-artifact

## Overview

This is an **OPTIONAL** skill that lives alongside the canonical
development_result format doc at
`.agent/artifact-formats/development_result.md`. Use it as a quick lookup
before submitting a development_result artifact, not as a substitute for the
format doc. The format doc is the source of truth for every required field,
the canonical-reference matching rule, and the proof-entry contract.

**Skill name vs MCP tool name.** This skill is named
`submit-development-result-artifact`. It is a separate name from the generic
MCP tool `ralph_submit_artifact`, which is the active submission entry
point. Do not conflate the two: the MCP tool for the development result is
`ralph_submit_artifact` with `artifact_type="development_result"`.

## When to Use

Use this skill when you are about to call `ralph_submit_artifact` to report
the outcome of a development task and the proof-entry contract is not
obvious from the plan/feedback alone. It is the right skill for the
canonical development_result artifact only — for commit_message,
commit_cleanup, issues, fix_result, smoke_test_result, or analysis-decision
artifacts, use the companion `submit-artifact` skill instead.

## Core Flow (one-shot)

1. Read `.agent/artifact-formats/development_result.md` once. It defines
   every required field, the canonical-reference matching rule, the proof
   contract, and the `status` closed enum. Treat it as a contract you must
   match exactly.
2. Set the required fields:
   - `status` — `"completed"` if the task is fully done, `"partial"` if more
     work is needed.
   - `summary` — a non-empty string describing what was done.
   - `files_changed` — a non-empty string listing the files that were
     modified (one per line with a `- ` prefix is a good format).
3. Build the proof entries when proof policy is enabled:
   - `plan_items_proven` — one entry per plan step OR per assigned work unit:
     - For step plans, `plan_item` must EXACTLY equal the canonical step
       reference `"Step N: <title>"` from the staged plan, where `N` is the
       step number and `<title>` is the step title as written. No rewording,
       no abbreviation, no case change.
     - For work_units plans, `plan_item` must EXACTLY equal the
       `unit_id` of the assigned work unit from the staged plan.
     - `proof` — a concrete, evidence-bearing string (file path + line
       range, test command output, observed behavior).
   - `analysis_items_addressed` — one entry per prior `how_to_fix` item when
     analysis feedback exists:
     - `how_to_fix_item` must be a verbatim copy of the prior analysis text.
       Copy the exact string, do not paraphrase.
     - `proof` — concrete evidence of how the item was resolved.
4. When `status="partial"`, the `next_steps` and `continuation` fields are
   REQUIRED:
   - `next_steps` — describes what still needs to be done.
   - `continuation` — an object like `{"prior_session_id": "<current-session-id>"}`
     so the next agent knows which session to continue from.
5. Build the inner payload as a plain JSON object (e.g.
   `{"status": "completed", "summary": "...", "files_changed": "...",
   "plan_items_proven": [...]}`).
6. Pass the inner payload as `content` either as the native JSON object
   or as a JSON-serialized string. Do NOT wrap it in an outer `{"type": ..., "content": ...}`
   envelope — Ralph Workflow adds artifact metadata itself.
7. Call
   `ralph_submit_artifact({"artifact_type": "development_result", "content": {"status": "completed", "summary": "...", "files_changed": "..."}})`.
8. After the submit success text, call `ralph_declare_complete(summary="development_result")`.

**Minimal one-shot happy-path envelope** for a completed development result:

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"completed\", \"summary\": \"Implemented the feature.\", \"files_changed\": \"- src/main.py\", \"plan_items_proven\": [{\"plan_item\": \"Step 1: Add validation\", \"proof\": \"Updated src/main.py lines 10-25; tests/test_x.py::test_validation passes.\"}], \"analysis_items_addressed\": [{\"how_to_fix_item\": \"Add test for edge case\", \"proof\": \"Added the regression test and verified it passes locally.\"}]}"
}
```

**Minimal one-shot happy-path envelope** for a partial development result:

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"partial\", \"summary\": \"Core work is done but verification is still pending.\", \"files_changed\": \"- src/main.py\", \"next_steps\": \"Run the remaining verification command and confirm the output.\", \"continuation\": {\"prior_session_id\": \"<current-session-id>\"}, \"plan_items_proven\": [{\"plan_item\": \"Step 1: Add validation\", \"proof\": \"Updated src/main.py lines 10-25.\"}], \"analysis_items_addressed\": []}"
}
```

## Recovery from a Bad Payload

When `ralph_submit_artifact` rejects a `development_result` payload, the
helper `_raise_format_doc_error` raises an `InvalidParamsError` whose
message points at `.agent/artifact-formats/development_result.md` and names
`ralph_submit_artifact` as the retry tool. Read the message, then:

1. If the helper `_artifact_content_format_error` is raised, your payload
   is missing the `content` field. Re-issue the call with `content` set to
   a native JSON object or JSON-serialized string.
2. If validation complains about `status`, you supplied something other
   than `"completed"` or `"partial"`. Use one of those two values exactly.
3. If validation complains about `summary` or `files_changed`, you supplied
   an empty string. Both fields are required and must describe the work
   actually done.
4. If validation complains about `next_steps` or `continuation`, you set
   `status="partial"` but did not include the continuation pair. Both
   `next_steps` and `continuation` are required for partial results.
5. If validation complains about `continuation`, you passed a bare string
   instead of the `{"prior_session_id": "..."}` object shape.
6. If proof policy rejects the artifact, the downstream proof-validation
   step found a `plan_item` or `how_to_fix_item` reference that does not
   EXACTLY match a real plan step, work unit id, or analysis item, OR you
   supplied duplicate entries. Re-fetch the plan and the prior analysis
   artifact, copy the canonical reference strings verbatim, and deduplicate
   before resubmitting.

**Worked retry envelope** for a `_raise_format_doc_error` style failure
on `development_result`:

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"completed\", \"summary\": \"Implemented the feature.\", \"files_changed\": \"- src/main.py\", \"plan_items_proven\": [{\"plan_item\": \"Step 1: Add validation\", \"proof\": \"Updated src/main.py lines 10-25.\"}], \"analysis_items_addressed\": []}"
}
```

**Worked retry envelope** for a `_artifact_content_format_error` style
failure (missing `content`):

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"completed\", \"summary\": \"Implemented the feature.\", \"files_changed\": \"- src/main.py\"}"
}
```

## Source of Truth Reference

- `.agent/artifact-formats/development_result.md` — the canonical schema
  for the development_result artifact. Bundled by Ralph Workflow and
  materialized into the workspace on demand. Every required field, the
  canonical-reference matching rule, and the proof-entry contract are
  defined here.
- `.agent/artifact-formats/artifact_formats_index.md` — the index that
  lists every supported `artifact_type` (including `development_result`)
  and points to each format doc.

If this skill and the format doc ever disagree, the format doc wins.

## Common Mistakes

- Treating this skill as authoritative. The format doc at
  `.agent/artifact-formats/development_result.md` is the source of truth;
  this skill is a quick pointer, not a substitute.
- Conflating `submit-development-result-artifact` (this skill) with the
  MCP tool `ralph_submit_artifact`. The MCP tool is the active submission
  entry point; the skill is the passive reference document.
- Rewording, abbreviating, or paraphrasing the canonical `plan_item`
  reference. `plan_item` must EXACTLY equal `"Step N: <title>"` as written
  in the staged plan (or the assigned `unit_id` for work_units plans).
  Stripping the `"Step N: "` prefix, changing case, or trimming trailing
  whitespace all fail validation.
- Paraphrasing the canonical `how_to_fix_item` reference. The string must
  be a verbatim copy of the prior analysis text.
- Submitting duplicate `plan_item` or `how_to_fix_item` values. Each entry
  must be distinct.
- Inventing extra proof entries that do not correspond to a real plan
  step, work unit id, or analysis item. The downstream proof-validation
  step rejects unknown references and re-prompts.
- Setting `status="partial"` without also providing `next_steps` and
  `continuation`. Both are required for partial results.
- Setting `continuation` to a bare string. The `continuation` field must
  be an object shaped like `{"prior_session_id": "<your-session-id>"}`.
- Using any `status` value other than `"completed"` or `"partial"`. The
  closed enum has exactly two values.
- Using `content_path` instead of `content`. Use `content` with a native JSON object
  or JSON-serialized string; `content_path` is reserved for non-agent callers.
- Treating a native JSON object as invalid. `content` may be a native JSON
  object or a JSON-serialized string; keep the outer
  `{"artifact_type": "development_result", "content": ...}` envelope either way.
## Red Flags - STOP and Start Over

- "I have read the format doc so I do not need the skill." STOP. The
  skill is a per-tool retry envelope; the format doc is the schema. They
  cover different failure modes.
- "The skill is OPTIONAL therefore ignorable." STOP. The OPTIONAL marker
  means the agent may consult the skill, not that the agent may skip the
  source-of-truth format doc. The skill names the format doc explicitly.
- "I will paraphrase the `plan_item` reference." STOP. The `plan_item`
  field must EXACTLY equal `"Step N: <title>"` as written in the staged
  plan (or the assigned `unit_id` for work_units plans). Stripping the
  `"Step N: "` prefix, changing case, or trimming trailing whitespace
  all fail validation.
- "I will paraphrase the `how_to_fix_item` reference." STOP. The string
  must be a verbatim copy of the prior analysis text — do not rewrite,
  abbreviate, or reorder it.
- "I will skip `next_steps` and `continuation` for a partial result."
  STOP. Both are required when `status="partial"`. A partial result
  without `continuation` is rejected as a downstream agent has no
  session to continue from.
- "I will reuse a `plan_item` from a different plan." STOP. The proof
  validator matches `plan_item` against the staged plan that is
  currently being proven; a value from a prior plan fails as an unknown
  reference and re-prompts.
