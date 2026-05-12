# Policy Explanation

Ralph Workflow can render a human-readable explanation of the active policy configuration with a single command. Use it when you want to sanity-check your pipeline before a run or document the workflow your team is relying on.

## The command

```bash
ralph --explain-policy
```

This reads the active `pipeline.toml` — project-local `.agent/pipeline.toml` when present, otherwise the bundled defaults — and prints a structured summary to stdout.

To inspect a custom policy directory instead:

```bash
ralph --explain-policy --explain-policy-dir /path/to/policy/dir
```

## Fast validation without explanation

If you only want a pass/fail validation without the full explanation output, use:

```bash
ralph --check-policy
```

This validates the same policy source as `--explain-policy` and prints a brief summary:

```
Policy OK: /path/to/.agent
  phases: 7
  drains: 11
  artifact contracts: 5
  loop counters: 2
  budget counters: 1
  workflow fallbacks: 0
  terminal failure phase: failed_terminal
```

Exit codes: 0 = valid, 2 = `PolicyValidationError`, 1 = other error.
`--check-policy` is useful in CI scripts or pre-flight hooks where you want to catch
invalid policy before starting a run. Accepts `--explain-policy-dir` for a custom
directory.

## What the output shows

The explanation covers all policy-declared elements:

| Section | What it contains |
|---------|-----------------|
| **WORKFLOW DIAGRAM** | ASCII diagram showing phases, routing edges, decision branches, loopbacks, and terminal markers |
| **Entry phase** | The phase where every run starts |
| **Terminal phase** | The phase that marks successful completion |
| **Phases** | Each declared phase with its role, drain, and key routing |
| **Loop counters** | Iteration counters with their names and caps |
| **Budget counters** | Outer-progress counters with names and budget-tracking flag |
| **Terminal outcomes** | All phases declared as terminal with their outcome type |
| **Parallel execution** | Whether parallel fan-out is configured and its source |
| **Recovery** | Cycle cap and where terminal failures route |

## Workflow diagram

The ASCII diagram is the first visual output from `--explain-policy`. It shows:

- **Boxed phase nodes** — each phase rendered as a box with its name and role
- **Entry marker** — `=ENTRY=>` marks the starting phase
- **Happy-path arrows** — `|` and `v` connect phases on the success path
- **Decision branches** — `+--[decision_name]--> target` shows routing for specific decisions
- **Loopback arrows** — `<<==[loopback]== returns to 'target'` marks phases that loop back; `>> RE-ENTRY at target` shows the re-entry point so the direction is unambiguous
- **Terminal markers** — `==SUCCESS==>` or `==FAILURE==>` marks terminal outcomes
- **Fanout annotations** — `>>> FAN_OUT (max_workers=N, max_units=M, post_fanout_verify=yes/no) >>>` before phases with parallelization
- **Loop annotations** — `[loop: counter=NAME, max=N]` on phases with bounded iteration

```
=ENTRY=>
+----------------+
|    planning    |
| role=execution |
+----------------+
    |
    v
>>> FAN_OUT (max_workers=8, max_units=50, post_fanout_verify=no) >>>
+----------------+
|  development   |
| role=execution |
+----------------+
    <<==[loopback]== returns to 'development'
    >> RE-ENTRY at development
<<< REJOIN
    |
    v
[loop: counter=development_analysis_iteration, max=3]
+----------------------+
| development_analysis |
|    role=analysis     |
+----------------------+
    +--[failed]--> development
    +--[request_changes]--> development
    <<==[loopback]== returns to 'development'
    [LOOPBACK: counter=development_analysis_iteration, max=3]
    >> RE-ENTRY at development
    |
    v
+--------------------+
| development_commit |
|    role=commit     |
+--------------------+
    |
    v
...
==SUCCESS==>
+-----------------+
| failed_terminal |
|  role=terminal  |
+-----------------+
==FAILURE==>

Legend:
  =ENTRY=>                    pipeline entry point
  ==SUCCESS==>                terminal success outcome
  ==FAILURE==>                terminal failure outcome
  +--[decision]-->            analysis decision branch
  <<==[loopback]==            loopback to earlier phase
  +--[workflow_fallback]-->   fallback on chain exhaustion
  >>> FAN_OUT ...             parallel worker fan-out
  <<< REJOIN                  workers rejoin after fan-out
```

### Reading the diagram

| Glyph | Meaning |
|-------|---------|
| `+--name--+` | Phase box with name and role |
| `=ENTRY=>` | Entry phase — where every run starts |
| `==SUCCESS==>` | Terminal success — marks a phase declared with `terminal_outcome='success'` |
| `==FAILURE==>` | Terminal failure — marks a phase declared with `terminal_outcome='failure'`; only policy-declared terminal phases receive this marker |
| `` <<==[loopback]== returns to 'X' `` | Loopback edge — routes BACK to phase X on loopback signal |
| `` >> RE-ENTRY at X `` | The phase where control re-enters after a loopback |
| `[LOOPBACK: counter=N, max=M]` | Loopback consumes loop counter N; present when loopback increments a loop counter |
| `+--[decision]--> Y` | Decision branch — routes to Y when decision is emitted |
| `` >>> FAN_OUT (max_workers=N, max_units=M, post_fanout_verify=yes/no) >>> `` | Fan-out — phase fans out to parallel workers |
| `` <<< REJOIN `` | Workers rejoin after fan-out completes |
| `[loop: ...]` | Loop annotation — phase has bounded iteration |

## Explanation sentences

The structural breakdown appends explanation sentences per phase for every routing surface.
Four sentence forms are generated:

| Form | Example |
|------|---------|
| Decision route | `Explanation: phase 'development_analysis' routes to 'development_commit' because the configured decision was 'completed'.` |
| Terminal outcome | `Explanation: when reached, the run terminates because the workflow policy declares phase 'complete' as a terminal 'success' outcome.` |
| Bypass route | `Explanation: phase 'review' bypasses to 'review_commit' when the configured outcome is 'review_clean'.` |
| Loopback cap | `Explanation: phase 'development_analysis' loops back to 'development' until 3 attempts are exhausted, after which the run terminates.` |

These sentences make it possible to answer "why did Ralph Workflow route here?" from the explanation output alone, without reading `pipeline.toml` or the runtime code.

## Structural breakdown

The second section provides the full structured breakdown:

```
RALPH WORKFLOW — ACTIVE POLICY EXPLANATION
======================================================================

Entry phase  : planning
Terminal phase: complete

Terminal outcomes:
  success    → complete

----------------------------------------------------------------------
PHASES
----------------------------------------------------------------------

  Phase: planning [ENTRY]
    Role       : execution (agent runs code)
    Drain      : planning
    Chain      : planning → agents: [claude]
    Retry      : up to 3 retries per agent, then fail
    On success → development
    On failure → pipeline fails (no on_failure route)

  Phase: development
    Role       : execution (agent runs code)
    Drain      : development
    Chain      : development → agents: [claude, opencode]
    Retry      : up to 3 retries per agent, then fall back to next agent
    On success → development_analysis
    On failure → pipeline fails (no on_failure route)
    On loopback → development

  Phase: development_analysis
    Role       : analysis (agent reviews output, decides next step)
    Drain      : development_analysis
    Chain      : development_analysis → agents: [claude]
    Retry      : up to 3 retries per agent, then fail
    On success → development_commit
    On failure → pipeline fails (no on_failure route)
    On loopback → development
    Decisions:
      completed            → development_commit
      request_changes      → development
      failed               → development
    Loop       : counter='development_analysis_iteration', max=3
Explanation: phase 'development_analysis' routes to 'development_commit' because the configured decision was 'completed'.
Explanation: phase 'development_analysis' routes to 'development' because the configured decision was 'request_changes'.
Explanation: phase 'development_analysis' routes to 'development' because the configured decision was 'failed'.
Explanation: phase 'development_analysis' loops back to 'development' until 3 attempts are exhausted, after which the run terminates.

  Phase: development_commit
    Role       : commit (agent commits changes)
    Drain      : development_commit
    Chain      : development_commit → agents: [claude]
    Retry      : up to 3 retries per agent, then fail
    On success → complete
    On failure → failed_terminal
    Commit     : increments 'iteration'
                 resets loop counters: ['development_analysis_iteration']
                 requires artifact: yes
    When is commit required? When this phase is active and the agent
      produces changes that need to be committed.
Explanation: after commit phase 'development_commit' with budget_state 'remaining' → routes to 'planning' because the workflow policy declares this post_commit_route
Explanation: after commit phase 'development_commit' with budget_state 'exhausted' → routes to 'complete' because the workflow policy declares this post_commit_route
Explanation: after commit phase 'development_commit' with budget_state 'no_review' → routes to 'complete' because the workflow policy declares this post_commit_route

  Phase: complete [TERMINAL]
    Role       : terminal (pipeline ends here)
    Drain      : complete
    Terminal outcome: success
    On loopback → complete
Explanation: when reached, the run terminates because the workflow policy declares phase 'complete' as a terminal 'success' outcome.

----------------------------------------------------------------------
LOOP COUNTERS
----------------------------------------------------------------------
  development_analysis_iteration: max=3 — Development analysis loop iteration counter
  planning_analysis_iteration: max=10 — Planning analysis loop iteration counter

----------------------------------------------------------------------
BUDGET COUNTERS
----------------------------------------------------------------------
  iteration: tracked (exhaustion matters) — Development iteration counter (developer cycles)

----------------------------------------------------------------------
PARALLEL EXECUTION
----------------------------------------------------------------------
  Fanout phase : development
  Max workers  : 8
  Max work units: 50
  Require allowed_directories: yes
  When is parallel execution allowed? When the planning artifact declares multiple work_units (up to 50) for phase 'development'.

----------------------------------------------------------------------
RECOVERY POLICY
----------------------------------------------------------------------
  Max recovery cycles : 200
  Terminal failure route: failed_terminal
  Session preserved on: agent

======================================================================
```

## Why this routed here

Every routing decision the pipeline makes traces back to a single declared field in
`pipeline.toml`. The explanation output makes that trace explicit.

| Runtime event | Explanation sentence source |
|---------------|-----------------------------|
| Analysis decision → phase | `phases.<name>.decisions.<decision>.target` |
| Terminal pipeline outcome | `phases.<name>.terminal_outcome` |
| Review bypass | `phases.<name>.bypass_routes.<outcome>` |
| Loop cap exhausted | `loop_counters.<iteration_state_field>.default_max` |
| Verification failure | `phases.<name>.verification.on_failure_route` |
| Parallel execution rejected | Absence of `phases.<name>.parallelization` |
| Post-commit route | `[[post_commit_routes]]` entry matching phase and budget_state |

When a run routes somewhere unexpected, run `ralph --explain-policy` and find the
corresponding `Explanation:` sentence. The sentence names the exact policy field that
produced the route. If the field is wrong, update `pipeline.toml`; if the field is
correct but the runtime ignores it, that is a bug.

To confirm which specific decision produced a route, check the run transcript for the
phase's artifact decision or review outcome, then cross-reference against the matching
`Explanation:` sentence in the explanation output.

## Why this is useful

Reading the explanation output answers "what will Ralph Workflow do?" without reading
TOML files. It is the machine-enforced statement of how the active policy routes work.

The workflow diagram provides a quick visual overview of the pipeline shape, while
the structural breakdown provides complete details for deep inspection.

The explanation output is deterministic: for the same `pipeline.toml` the output is always
the same. Pin it in a review artifact, CI log, or runbook to record the exact workflow
a run used.

Note: the ASCII example in this document is illustrative. Regenerate it with
`ralph --explain-policy` when the renderer changes.

## How the explanation is generated

The command calls `ralph.policy.explain.explain_policy()` which traverses the validated
`PipelinePolicy` in memory and produces a `PolicyExplanation` dataclass. That dataclass
is rendered by `ralph.policy.render.render_explanation_ascii()` into the ASCII diagram
and by `ralph.policy.render.render_explanation_text()` into the structured format shown above.

Because it runs against the already-validated policy, an explanation can only be
produced if the policy is complete. If `pipeline.toml` is invalid, the command exits 1
and prints a `PolicyValidationError` to stderr instead of partial output.

## Q&A

**Q: How do I prove policy is the source of truth?**
A: `tests/test_custom_policy_workflow.py` constructs a fully renamed policy (phases `design`/`build`/`audit`/`sign_off`/`done`, counter `cycles`, loop `audit_round`) and exercises the reducer to confirm no built-in name is secretly meaningful.

## Related pages

- [Configuration](configuration.md) — `pipeline.toml` policy fields reference
- [Concepts](concepts.md) — phase roles, loop counters, and budget counters explained
- [Troubleshooting](troubleshooting.md) — common `PolicyValidationError` patterns
