# Repeat Failure Response Framework

Use this framework when a process failure has become a long-running fiasco: repeated user reminders, recurring drift, contradictory artifacts, and obvious evidence that the loop is not self-repairing.

This is not a normal watchdog pattern.
This is the **maximum-severity self-repair pattern** for an agent/process stack that has already proven it cannot be trusted to heal itself with ordinary checks.

## When to activate it

Activate this framework immediately when any of these are true:

- the user has to repeat the same top-level complaint
- multiple runs produce activity without convergence
- verifier, checker, agentic, or status artifacts disagree
- the process keeps falling back into the same class of failure
- the human is acting as the escalation layer instead of the process
- there is any serious doubt that the stack is actually fixed

Repeated user reminders are not normal follow-up.
They are proof that the process, monitor, verifier, and repair loop have already failed.

## Core principle

**When a process becomes a fiasco, create a frequent-checking, broad-authority, self-reporting remediation loop that has permission to fix anything and everything locally until the whole stack is green.**

That means:

- frequent interval checks
- broad local repair authority
- explicit reporting every run
- parallel-agent escalation when helpful
- self-deactivation only when the whole stack is provably healthy
- permanent lightweight monitoring after the aggressive layer stands down

## Required architecture

### 1. Permanent interval watchdog

Install a permanent interval watchdog job (for example every 10 minutes) that:

- checks current process health
- reports status every run
- escalates into active repair when unhealthy
- stays installed even after the temporary aggressive repair layer deactivates

This permanent watchdog must never depend on the user to notice recurrence.

### 2. Temporary aggressive self-heal script

Create a temporary repair script for the specific fiasco.
Its job is to:

- run the full relevant health stack
- attempt safe local remediation
- write explicit status artifacts
- **refuse to self-delete on its own judgment alone**
- self-delete only if the entire stack is actually healthy **and an independent parallel-agent signoff artifact says so**

This is the aggressive layer, not the permanent layer.
It exists to force convergence during the crisis.

Required rule: the aggressive self-heal layer must never be allowed to deactivate itself solely because its own local checks look green. A separate parallel agent must explicitly sign off that the stack is truly fixed.

### 3. Broad repair authority

The watchdog/orchestrator must have authority to use every safe local path, including:

- direct file edits/writes/patches
- rerunning checker/editorial/agentic/verifier steps
- fixing monitor/verifier/remediator code
- fixing routing, docs, config, or process artifacts
- spawning parallel subagents for independent judgment and repair
- updating its own cron/supervision behavior when needed

Do not build a fiasco-response loop that can only observe.
Observation-only is not enough at this severity.

### 4. Parallel-agent escalation

When the issue is still broken after ordinary repair attempts, escalate to parallel agents.
Parallel agents are also required for **deactivation signoff** of the aggressive self-heal layer.
Typical roles:

- **surface remediator** — fixes public-facing surfaces/content/routes
- **verifier-path remediator** — fixes artifact truth, verifier logic, stale-state handling
- **route/ownership auditor** — checks cross-surface coherence and ownership contradictions
- **independent critic** — judges whether the system still feels broken holistically

Parallel agents are for converging faster and getting independent judgment, not for chaotic uncontrolled rewriting.

Required escalation rule: once repeat failure persists past ordinary repair attempts, the active watchdog prompt/runtime must explicitly treat this as an all-hands event and instruct itself to spawn parallel subagents for independent remediation/verifier-path work unless a platform safety boundary blocks it.
This is not satisfied by writing an escalation reason into a status file. The runtime must actually loop back into fixer → evaluator → fixer → evaluator behavior until independent signoff passes or a real platform boundary blocks the next step.

### 5. Parallel signoff artifact

The framework must maintain a machine-readable signoff artifact written by an independent parallel agent.
That artifact should include at least:

- signer identity / role
- checked-at timestamp
- current docs/process state fingerprint
- whether verifier is green and current
- whether agentic review is green and current
- whether repeated-failure conditions are cleared
- explicit `approvedToDeactivate: true|false`

If the signoff artifact is missing, stale, mismatched to the current docs/process fingerprint, or not explicitly approving deactivation, the aggressive self-heal layer must stay active.
Three consecutive verifier failures are an automatic process/framework escalation and must be persisted in machine-readable runtime state, not merely mentioned in prose.

### 6. Mandatory reporting

Every interval run must report:

1. healthy or still broken
2. concrete actions taken this run
3. top remaining blocker
4. whether any aggressive repair layer deactivated
5. whether repeat-failure escalation is now mandatory and whether parallel subagents/all-hands repair were triggered or remain blocked

If the aggressive layer self-deletes because the stack is green, the user must be told explicitly.

## Convergence rules

A fiasco-response loop is not allowed to declare success on partial improvement.

Required success conditions:

- all primary health checks are green
- no stale contradictory artifacts remain
- the relevant agentic/holistic review is green
- the verifier is green and current
- the loop itself no longer says the user should need to repeat the complaint
- no human intervention is required
- an independent parallel-agent signoff artifact approves deactivation for the current stack fingerprint

If any layer still disagrees, the stack is still broken.

If the same failure class persists across repeated runs, the system should default to stronger action, not more waiting: broader repair authority, parallel subagents, and explicit refusal to stand down.

## Failure rules

The loop must fail loudly when:

- output drift occurs without real state improvement
- a runner claims repair but docs/process state did not change
- a verifier artifact lags current reality
- a sub-review stalls, times out, or returns malformed output
- repeated recurrence appears after an earlier claimed fix
- the human has to mention yet another part of the same fiasco

## Recommended operating pattern

1. detect the fiasco
2. install/upgrade the permanent interval watchdog
3. create the temporary aggressive self-heal script
4. give the watchdog broad remediation authority
5. force immediate run; do not wait for the next interval
6. escalate to parallel agents if still unhealthy
7. keep iterating until the entire stack is green
8. let the temporary aggressive layer self-delete only after real convergence and independent parallel-agent signoff for the current stack state
9. leave the permanent watchdog in place to catch recurrence

## The docs-agent case

This framework was derived from the Ralph docs-agent fiasco, where the process failure lasted far too long because:

- docs drift was real
- the monitor was too weak
- the remediator was partially bad
- the verifier could lag/stale-fail
- artifacts could disagree
- the user had to keep restating the same problem

That is exactly the kind of failure this framework is for.

## Non-negotiable rule

If the human has to keep saying the same thing again and again, the process is already broken enough to justify this framework.
Do not wait for permission to escalate.
