# plan artifact format

A plan is one readable Markdown document. Use the structure that makes the
work clearest; headings and step IDs are optional unless a downstream reader
uses them. Keep it short enough to reread, and state each commitment once next
to the work it describes.

## The only blocking rule

`PLAN001` is the sole error. It means the submission is not a plan: it is
empty or markup-only, has fewer than 100 characters of actual content, is
recognizably truncated (an unterminated frontmatter block, unclosed code fence,
dangling plan field label, final comma or function word, unclosed inline
bracket/parenthesis/backtick, or empty list bullet at EOF), or is clearly a
refusal, question, status update,
tool output, stack trace, or placeholder. Analysis and execution cannot proceed
without a plan to read.

Every other finding is advisory:

- **Warning** predicts a concrete cost to this run and says how to resolve it.
- **Info** is an observation worth a second look.

Warnings and info never make a plan invalid. If proceeding is the informed
choice, record the reason under `## Validation Overrides` as
`- [RULE-ID] reason`. Override reasons remain visible in tool responses and
plan history; stale overrides are reported as info.

## Useful default shape

A good plan normally makes four phases visible in the order they will happen:
Orient, Characterize current behavior, Change, and Verify. It should state the
outcome, real files or areas already inspected, risks or unknowns, and
completion evidence. These are authoring guidance, not required headings.

```markdown
---
type: plan
---
## Work

### [S-1] Characterize the current retry behavior
Inspect the current default and add a focused regression that captures it.

Files:
- modify tests/test_retry.py

### [S-2] Change and prove the default
Update the retry implementation and run the focused test.

Files:
- modify ralph/retry.py
Depends on: S-1
Verify: pytest tests/test_retry.py -q
Expect: the focused retry tests pass with exit code 0
```

Custom headings, prose plans, and an omitted conventional section are valid
when the plan remains understandable. For a larger conventional sample, see
`.agent/artifact-formats/examples/plan.md`. For parallel work, use `## Work Units`
or `## Parallel Plan` only when the executor will consume those markers;
otherwise describe independent work naturally.

## Standard artifact tools

Submit a complete document with `ralph_submit_md_artifact` using
`artifact_type: plan`; `ralph_verify_md_artifact` previews the same result.
For a similar revision, use `ralph_edit_md_artifact` on the staged draft. It
submits automatically when the edited draft has no errors. Use
`ralph_stage_md_artifact` with `mode="replace_all"` only for a wholesale rewrite;
inspect with `ralph_get_md_draft` and submit an assembled draft with
`ralph_finalize_md_artifact`. Staging is not submission: a retained draft that
differs from the canonical document blocks phase completion until it is
resubmitted or deliberately abandoned with `ralph_discard_md_draft`.

Do not write `.agent/artifacts/plan.md` directly. After a valid submission,
call `declare_complete` as the final action.
