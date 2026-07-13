# policy_remediation_analysis_decision artifact format

## What you are doing

You are reporting the outcome of a project-policy remediation review: whether the policy files another agent just wrote are TRUE, whether the gates they declare actually RESOLVE, and whether any gate scripts obey `docs/ralph-workflow-policy/gate-script-policy.md`.

You can also submit this using `artifact_type: "analysis_decision"` when your session drain is `policy_remediation_analysis`.

## The one rule that decides your status

**If the script is broken, call it. If the script works and the code it tests is broken, that is not your business.**

Route back with `request_changes` when the fault is the REMEDIATION AGENT'S:

- a declared gate command does not resolve (exit 127, "No rule to make target", missing script)
- a gate script is broken (syntax error, unbound variable, bad shebang, fails open)
- a script calls a phantom tool, flag, library, or path that does not exist
- a gate is hollow (`echo ok`, a test target matching zero tests)
- a `RALPH-FACT` is fabricated or contradicts the repository
- a gate script violates the gate-script policy (no strict mode, unbounded, insecure, non-portable on a Windows-supporting project, untested)

Do NOT route back when the fault is the PROJECT'S:

- a gate resolves and runs correctly, but the unit tests it invokes fail
- a gate resolves and runs correctly, but the underlying code is broken (type errors, lint findings, a red build)

A gate that correctly reports a real failure is a WORKING gate. Reporting it as `request_changes` would trap the run in a loop trying to fix a codebase that is not yours to fix.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `policy_remediation_analysis_decision` and `content` set to either a native JSON object or a JSON-serialized string containing your decision payload.

```json
{
  "artifact_type": "policy_remediation_analysis_decision",
  "content": "{\"status\": \"completed\", \"summary\": \"All 11 policy facts verify against the repo; every declared gate resolves.\"}"
}
```

## Required fields (inside content)

- `status` — must be `"completed"` if the policy files are true and the gates resolve, `"request_changes"` if the remediation agent must fix something, or `"failed"` if the review itself could not be completed
- `summary` — a non-empty string describing what the review found

## Optional fields (inside content)

- `what_came_up_short` — an array of strings listing what is wrong (required when status is `"request_changes"` or `"failed"`, omit when status is `"completed"`)
- `how_to_fix` — an array of strings with concrete steps to resolve each problem (required when status is `"request_changes"` or `"failed"`, omit when status is `"completed"`)

## Complete example

```json
{
  "artifact_type": "policy_remediation_analysis_decision",
  "content": "{\"status\": \"completed\", \"summary\": \"Every RALPH-FACT verifies against the repository and every declared gate command resolves.\"}"
}
```

## Retry-ready non-completed example

```json
{
  "artifact_type": "policy_remediation_analysis_decision",
  "content": "{\"status\": \"request_changes\", \"summary\": \"Two declared gates do not exist and one script calls a phantom tool.\", \"what_came_up_short\": [\"verification-policy.md declares 'make verify-all', but no such make target exists (exit 2: No rule to make target)\", \"scripts/check.sh invokes 'shellcheck --strict', which is not a real shellcheck flag\"], \"how_to_fix\": [\"Replace the declared gate with the real entry point 'make verify', or create the verify-all target\", \"Remove the invented --strict flag; use -S style if a severity floor is wanted\"]}"
}
```

## Common mistakes

- Do NOT report `request_changes` because the project's tests fail or its build is red — that is the project's problem, not the remediation agent's. Only the SCRIPT and the POLICY are in scope.
- Do NOT report `completed` without actually probing the declared gate commands. A gate you did not run is a gate you did not verify.
- Do NOT attempt to fix what you find. You have no workspace write capability by design; report it and route back.
- Do NOT use any status other than `"completed"`, `"request_changes"`, or `"failed"`
- Do NOT leave `summary` empty
- Do NOT omit `what_came_up_short` or `how_to_fix` when status is `"request_changes"` or `"failed"`
- Do NOT include `what_came_up_short` or `how_to_fix` when status is `"completed"`

## Dumb-proof checklist

- Did you actually RUN each declared `RALPH-COMMAND` as a bounded probe?
- For each failure you saw, did you ask "is this the script's fault or the project's fault?" and only route back for the script's?
- Did you check every gate script against `gate-script-policy.md`?
- Did you verify that every tool and flag the scripts reference actually exists?
- Did you set `status` to `"completed"`, `"request_changes"`, or `"failed"`?
- Did you write a non-empty `summary`?
- Did you include `what_came_up_short` and `how_to_fix` when status is not `"completed"` (and omit them when it is)?
