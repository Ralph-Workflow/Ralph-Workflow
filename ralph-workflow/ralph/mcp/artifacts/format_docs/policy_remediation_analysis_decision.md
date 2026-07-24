# policy_remediation_analysis_decision artifact format

You are reporting the outcome of a project-policy remediation review:
whether the policy files another agent wrote are TRUE and whether the gates
they declare actually RESOLVE. Author markdown and submit with
`ralph_submit_md_artifact`
(`artifact_type: policy_remediation_analysis_decision`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/policy_remediation_analysis_decision.md`

## The one rule that decides your status

If the script is broken, call it. If the script works and the code it
tests is broken, that is not your business. Route back with
`request_changes` only for the remediation agent's faults: a declared gate
that does not resolve, a broken or hollow gate script, a phantom tool or
flag, a fabricated RALPH-FACT, or a gate-script-policy violation. A gate
that correctly reports a real project failure is a WORKING gate.

## Complete minimal example (completed)

```markdown
---
type: policy_remediation_analysis_decision
status: completed
---

## Summary

- [SUM-1] Every RALPH-FACT verifies against the repo and every declared gate resolves.
```

## Complete example (request_changes)

```markdown
---
type: policy_remediation_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] Two declared gates do not exist and one script calls a phantom tool.

## What Came Up Short

- [W-1] verification-policy.md declares 'make verify-all' but no such target exists.
- [W-2] scripts/check.sh invokes 'shellcheck --strict', which is not a real flag.

## How To Fix

- [FIX-1] Point the declared gate at the real entry point 'make verify'.
- [FIX-2] Remove the invented --strict flag; use -S style for a severity floor.
```

## Frontmatter

- `type` — required; `policy_remediation_analysis_decision`.
- `status` — required and closed: `completed`, `request_changes`, or `failed`.
  Any other value, including `done` or `wrong`, is a hard error. The
  diagnostic names all three accepted values; correct it and resubmit.

## Sections

- `## Summary` — required; exactly one item.
- `## What Came Up Short` — one item per problem; required (non-empty)
  when status is `request_changes` or `failed`, omitted when `completed`.
- `## How To Fix` — one concrete remediation per item; same
  required/omitted rule. Keep item IDs unique and stable.

## Hard errors vs warnings

Hard errors: missing or multiple Summary items; `request_changes`/`failed`
without non-empty What Came Up Short and How To Fix; wrong `type`;
duplicate item IDs; any grammar violation; or a `status` outside
`completed`, `request_changes`, and `failed`.
