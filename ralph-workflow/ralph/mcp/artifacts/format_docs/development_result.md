# development_result artifact format

You are reporting the outcome of a development task: what you did, what
changed, and what you verified. Author markdown and submit with
`ralph_submit_md_artifact` (`artifact_type: development_result`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/development_result.md`

## Free-form body

The frontmatter `status` is the only field the validator mechanically
checks. Below the frontmatter, write the development result in your own
words: what you did, what changed, how you verified the change, and
what remains. The body is the next agent's reading matter, not a
structure this artifact gates on. Coverage of the plan and of the
prior analysis is judged by development analysis, not checked
mechanically here.

A useful pattern is:

- One item under `## Summary` describing the outcome in plain language.
- One `## Files Changed` block listing the paths you touched.
- For each affected criterion or plan item, a short evidence line citing
  a reproducible command, a `path:line` anchor, or a focused test
  result.
- For `partial` or `failed`, state the concrete external action or
  in-scope continuation the next agent should pick up.
- `partial` means dispatch remaining independent work in parallel in the
  same run rather than handing each piece back as a separate `partial`.

There is no required section, no required field label, and no required
stable ID for individual items. The validator mechanically checks the
frontmatter `status` enum; everything else is read by the next agent.

## Frontmatter

- `type` — required; `development_result`.
- `status` — required and closed: `completed`, `partial`, or `failed`.
  Any other value, including `done` or `wrong`, is a hard error. The
  diagnostic names all accepted values; correct the frontmatter and
  resubmit.

## Example shape

```
---
type: development_result
status: completed
---

## Summary
- Implemented and verified the requested behavior.

Wrote what I did, what changed, and what I verified. The body is
free-form; the validator only checks the frontmatter ``status``.
```

## `partial` and `failed` outcomes

`status: partial` and `status: failed` are accepted at any point, but
neither is a shortcut around work the developer can perform. Use
`partial` only when verified progress exists and remaining required
work cannot be completed by any developer action available in the
current run. Examples include a physical-world action such as
unplugging a power cable, an operator-only credential or decision, or
an external system change outside the developer's authority.
Difficulty, elapsed time, an exhausted run budget, or ready work the
developer can still perform does not qualify. The `partial` decision
itself is role-aware: a coordinator who still owns independent ready
slices dispatches them in parallel rather than handing each one back
as a separate `partial`; a worker continues in-scope recovery within the
assigned unit per `shared/_no_exemption_for_failures.j2` (the worker
contract forbids dispatch, so the same "dispatch in parallel" rule
does not apply to a worker reading this format doc). After submitting
`partial`, call `declare_complete` once with `partial_reason` naming
that literal impossibility and required external action; no second
confirmation call is required. Use `failed` when no safe actionable
continuation exists under current evidence or authority; report the
blocker and evidence without promising another iteration. Neither
status decides whether the run ends.

## Hard errors vs warnings

Hard errors at any status: an unrecognized `status` value. The
unrecognized-`status` error reports the valid `completed` / `partial`
/ `failed` vocabulary.
