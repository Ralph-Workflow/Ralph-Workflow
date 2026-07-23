# plan artifact format

You are writing the implementation plan a development agent will execute
without re-planning. The plan is one markdown document; each list item's
text is a single-line compact JSON object carrying that entry's fields.

Author the markdown, lint with `ralph_verify_md_artifact`, and submit with
`ralph_submit_md_artifact` (`artifact_type: plan`). For a large plan,
stage drafts with `ralph_stage_md_artifact`, read them back with
`ralph_get_md_draft`, drop them with `ralph_discard_md_draft`, and submit
with `ralph_finalize_md_artifact`. To change one step later, call
`ralph_edit_md_plan_step` (actions: replace, insert, remove, move) — it
renumbers step IDs to `S-1..S-n` and remaps `depends_on` for you.

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/plan.md`

## Complete minimal example

```markdown
---
type: plan
---

## Summary

- [SUM-1] {"context":"foo() crashes on out-of-range indexes; clamp them without changing the public signature.","scope_items":[{"text":"Add a regression test for out-of-range indexes","category":"test"},{"text":"Clamp negative and oversized indexes in src/foo.py","category":"bugfix"},{"text":"Run the focused test to prove the fix","category":"test"}]}

## Skills MCP

- [SK-1] {"skills":["test-driven-development"]}

## Steps

- [S-1] {"title":"Add regression test","content":"Write tests/test_foo.py::test_clamp_out_of_range covering negative and oversized indexes.","step_type":"file_change","targets":[{"path":"tests/test_foo.py","action":"modify"}]}
- [S-2] {"title":"Clamp indexes in foo()","content":"Clamp the index into range at the top of foo() in src/foo.py.","step_type":"file_change","targets":[{"path":"src/foo.py","action":"modify"}],"depends_on":["S-1"]}
- [S-3] {"title":"Run the focused test","content":"Run the regression test and confirm it passes.","step_type":"verify","verify_command":"pytest tests/test_foo.py -q","depends_on":["S-2"]}

## Critical Files

- [CF-1] {"primary_files":[{"path":"src/foo.py","action":"modify"},{"path":"tests/test_foo.py","action":"modify"}]}

## Risks Mitigations

- [R-1] {"risk":"Clamping could mask a caller bug.","mitigation":"Log a warning when clamping fires and assert the regression test covers both bounds."}

## Verification

- [V-1] {"method":"pytest tests/test_foo.py -q","expected_outcome":"test_clamp_out_of_range passes with exit code 0"}
```

## Frontmatter

- `type` — required; `plan`.
- `schema_version` — optional integer.
- `intent_verb` — optional; one of add, fix, refactor, migrate, document,
  investigate, improve, configure, remove. Unknown values are coerced to
  `add` with a warning.

## Sections

Required (item counts in parentheses):

- `## Summary` (exactly 1) — object with `scope_items` (at least 3, each
  `{"text": ...}` plus optional `count` and `category`) and optional
  `context` and `intent`.
- `## Skills MCP` (exactly 1) — object with non-empty `skills` list and
  optional `mcps` list.
- `## Steps` (1 or more) — one object per step; see below.
- `## Critical Files` (exactly 1) — object with `primary_files` (at least
  one `{"path", "action"}` where action is create/modify/delete) and
  optional `reference_files` (`{"path", "purpose"}`).
- `## Risks Mitigations` (1 or more) — `{"risk", "mitigation"}` plus
  optional `severity` (low/medium/high/critical).
- `## Verification` (1 or more) — `{"method", "expected_outcome"}` plus
  optional `timeout_seconds` (1-3600) and `cwd`. Give a concrete expected
  outcome, never a vague "all tests pass". `method` must not start with
  `bash -c `, `sh -c `, or `eval ` (shell-invocation guard; `bash
  ./script.sh` is fine).

Optional: `## Constraints` (exactly 1 object), `## Design` (exactly 1
object, including acceptance criteria for larger tasks), `## Parallel
Plan` and `## Work Units` (1+ objects each; mutually exclusive —
agent-facing parallelization intent only).

## Steps

Step IDs are the stable references every other artifact uses (the
development result proves each step by its `S-n` ID), so they must use the
form `S-1`, `S-2`, … (no leading zeros), unique within the section.

Each step object requires `title` and `content`. Optional fields:

- `step_type` — file_change / action / research / verify (default action).
  A `file_change` step must declare at least one `targets` entry
  (`{"path", "action"}` with action create/modify/delete/read/reference);
  a `verify` step must declare `verify_command` or `location`.
- `depends_on` — list of step IDs, e.g. `["S-1"]`. Unknown IDs and
  dependency cycles are rejected.
- `priority`, `rationale`, `location`, `satisfies` (acceptance-criterion
  IDs shaped `AC-01`, only valid when `## Design` declares them),
  `expected_evidence` (`{"kind": file|command_output|test_name, "ref"}`).

## Hard errors vs warnings

Hard errors: a missing required section or wrong item count; an item whose
text is not a JSON object; malformed or duplicate item IDs; a step ID not
shaped `S-<number>`; `depends_on` naming an unknown step ID or forming a
cycle; a file_change step without targets or a verify step without
verify_command/location; empty `skills`, `scope_items` < 3, empty
`primary_files`; dangling `satisfies`/acceptance-criteria references;
declaring both Parallel Plan and Work Units; an `intent_verb` incompatible
with the scope categories it covers; the shell-invocation guard;
and size caps (4 MB document, 500 steps, 200 risks, 100 verification
steps, and per-field caps reported as `plan size violation: field=...`).

Warnings (value coerced, document accepted): unknown `intent_verb` (to
add), scope `category` (to other), `step_type` (to action), `priority`
(to medium), target `action` (to modify), evidence `kind` (to file), and
risk `severity` (to medium).
