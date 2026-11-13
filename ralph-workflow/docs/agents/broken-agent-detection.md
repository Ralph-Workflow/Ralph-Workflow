# Broken-Agent Detection

Ralph treats an agent as broken when circumstantial evidence shows that it cannot produce meaningful LLM output. The recovery action is fallover to the next configured agent, rather than retrying the same agent.

## Detection

`BROKEN_AGENT_OUTPUT_GRACE_SECONDS` is 12 seconds. It is intentionally shorter than the 120-second `NO_OUTPUT_AT_START_SECONDS` watchdog threshold.

Both subprocess and PTY line readers enforce the grace window while the process is still alive. If no meaningful output appears by the deadline, the reader terminates the process tree and raises `BrokenAgentExitError` with `reason="no_output"`.

A clean exit is checked before the normal resumable-exit path without waiting for that grace window. Empty bounded output, output made entirely of prompt echoes, and small no-meaningful-output cases each raise `BrokenAgentExitError` immediately. `no_llm_activity` is only treated as an immediate broken-agent classification when bounded output is structurally small (≤2 nonblank lines and ≤256 bytes total); larger output with `has_meaningful_output=False` falls through the missing-artifact path for resumable/retry handling. A credential marker in stderr, or in empty/structurally small bounded output, does the same, whether the harness exits with `rc=0` or nonzero; a substantial transcript that merely mentions credentials (an echoed retry prompt, the master prompt, or source under discussion) follows the resumable missing-artifact path instead. A credential-marked nonzero exit after the grace window remains a normal recoverable invocation failure.

For a settled fast subprocess exit, Ralph preserves the process's actual exit status and reads up to 64 KiB from its stderr pipe into the `BrokenAgentExitError` diagnostic. This is crash evidence, not provider attribution: use the status and stderr to investigate the local agent command, credentials, extension, or provider configuration; absence of stderr does not identify an external cause. PTY transports multiplex terminal output and do not provide a separate stderr pipe.

## Non-LLM Activity

`reason="no_llm_activity"` identifies a clean exit whose bounded output is a **small structural case** with watchdog-confirmed no meaningful LLM activity: nonempty output with ≤2 nonblank lines and ≤256 bytes total, typically lifecycle noise or simple harness chatter. It differs from `no_output`, where no classified output was seen, and `prompt_echo`, where every nonblank line was a deterministic echo of the input prompt. Substantial output above the structural threshold is routed through the normal resumable/retry path instead.

The completion check uses invocation age rather than time since the most recent activity, so continual lifecycle output cannot indefinitely hide this condition. Treat this reason as a credentials or provider-availability problem and follow the same fallover response as the other broken-agent reasons.

## Prompt Echoes

Harness output is not evidence of LLM work when it deterministically echoes the input prompt. The transport-neutral `_is_prompt_echo_line` helper recognizes only nonblank lines that either equal the complete stripped prompt or contain that complete prompt verbatim.

The inverse substring case is deliberately excluded. For example, `plan` is valid output even if the prompt contains the word `plan`.

Readers mark matching lines as harness echoes, so `IdleWatchdog.has_meaningful_output()` remains false. The completion gate raises `BrokenAgentExitError(reason="prompt_echo")` immediately when every nonblank output line is a prompt echo.

## Recovery

`BrokenAgentExitError` has `skip_same_agent_retries=True` and includes a credential/provider hint for operators. `FailureClassifier` maps it to `UnavailabilityReason.BROKEN_AGENT`, marks it unavailable, and resets its session.

The default `BROKEN_AGENT` backoff starts at a 5-second base, doubles on each repeat of the same failure, and saturates at the universal five-hour ceiling (18 000 000 ms) that every default unavailability reason shares. Recovery publishes the normal fallover event and advances the agent chain.

That per-failure unavailability backoff is a different mechanism from the ledger's unproductive-limit cooldown. One broken exit marks the agent temporarily unavailable; the ledger demotes an agent only after a run of unproductive attempts.

## Agent Failure Limits

Exactly one component decides that an agent has failed too many times: the agent failure ledger (`AgentFailureLedger`). The recovery controller, the pipeline reducer, and conflict-resolution selection are clients of that ledger — they record attempt facts into it, or apply the verdict it returns. No second tracker, counter, or cap may decide that an agent retries, is benched, or runs next; a seam that cannot reach the ledger fails its phase closed instead of granting another attempt.

An attempt is **unproductive** when it ends without a valid artifact. Two `[general]` settings bound unproductivity per phase and agent:

- `agent_max_consecutive_unproductive` (default `3`) — after that many consecutive unproductive attempts, the agent enters cooldown and the chain falls over to the next eligible agent.
- `agent_max_total_unproductive` (default `9`) — after that many unproductive attempts in total, the agent is spent for the phase.

A workspace change resets only the CONSECUTIVE count. The TOTAL count keeps every unproductive attempt, with or without a workspace change, so an agent that keeps editing files without ever producing the artifact still reaches the TOTAL limit and is spent. Producing a valid artifact clears the agent's history: both counts, the cooldown, and the spent marking are reset.

The cooldown starts at 60 seconds, doubles on each consecutive demotion, and is capped at 3600 seconds. Those durations are fixed and are not configurable in `ralph-workflow.toml`. When the cooldown expires the agent becomes eligible again and its consecutive count starts from zero. The ledger state is written into the run's persisted state, so the counts and the cooldown survive a restart.

A broken-agent exit skips same-agent retries, so that first failure advances to the next available agent when the chain has one; other unproductive attempts (a missing artifact, for example) retry the same agent until the consecutive limit demotes it. In an ordinary phase a sole-agent chain has nowhere to fall over to, so reaching the limit fails the phase with a clear diagnostic message instead of retrying indefinitely. Conflict resolution and integration are the exception: when every resolver candidate is demoted or otherwise ineligible, integration remains pending/waiting, the rebase state is preserved on disk, and resolution retries when a candidate comes off cooldown. That stop is pending work, not a failure — it never hands off to the operator and never reports a failed rebase or integration.

## Operator Log Lines

A demotion is logged once, when it happens:

```
Agent '<agent>' demoted in phase '<phase>' (consecutive=<n>, total=<n>, reason=<reason>)
```

Agent selection reports every candidate it skipped and why. The skip reasons are `cooldown` (the agent is still cooling down, reported with the milliseconds remaining and, when known, the cooldown reason), `unavailable` (the agent is marked unavailable by a failed invocation), `spent` (the agent is permanently demoted for the phase), and `lower_priority` (the agent was eligible, but a higher-priority candidate was selected).

## Operator Response

For a fast no-output subprocess exit, start with the `BrokenAgentExitError` exit code and stderr text. They distinguish a hard process crash from a clean no-output completion, but they do not by themselves prove why a provider failed. Then check the selected agent's credentials, command configuration, extension setup, and provider availability. A prompt-echo diagnosis also indicates that the harness started but the model did not provide meaningful output.

After correcting credentials or provider configuration, allow the unavailable-agent cooldown to expire or restart the run using the configured recovery workflow.
## Dead-Agent Verdict

An attempt that ends without a valid artifact is judged in four recorded layers. Only the verdict layer benches an agent, and "unknown" is a first-class answer that never benches.

**Facts.** Each attempt records `AttemptFacts`: event kinds in order, tool calls, workspace change as true, false or unknown (never collapsed), whether the artifact is valid, time to the terminal state, exit status, liveness observations (events in the window, subagent store updates), and a termination record. The termination record holds the issuer (`agent_exit`, `supervisor_idle`, `quota_detector`, `post_exit_watchdog`, `recovery_timeout`, `operator`, `unknown`), the return code the process manager observed, and the return code reported upward. When the two return codes differ the record is flagged. A signal-shaped return code (negative, or 129-159) or a missing return code without an attributed issuer is `unknown`, never `agent_exit`. Raw message text is for humans and audit only.

**Attribution.** Facts classify the end as `agent`, `runtime`, `task_or_environment` or `unknown`. A runtime-issued end (`quota_detector`, `supervisor_idle`, `recovery_timeout`, `post_exit_watchdog`, `operator`) or an unattributed end of a live session is labelled with its cause (`runtime_kill_quota_misread`, `runtime_kill_supervisor_idle`, `runtime_kill_post_exit_watchdog`, `runtime_kill_unattributed`, and so on) and never counts toward cooldown or spent state. A valid outcome, workspace writes, mid-run activity (tool calls, subagent updates) and peers failing identically point to the task or environment. An interactive turn end with subagents still pending — a stop-hook sentinel firing while the session is live and the artifact is missing — records no issuer, is attributed `runtime_kill_unattributed`, resolves `not_agent_fault` and never benches or counts toward the ledger, even though tool calls and subagent activity were observed. Attribution rules only clear an agent; they never convict one.

**Probe.** When attribution leaves the agent as a suspect, a canary probe runs the same CLI, model, credentials and environment on a fixed trivial task — write one marker file with exact content — with a mechanically checked result and a 30-second timeout. At most three probes run per pipeline run. The probe launches its scratch workspace under `<trusted_parent>/.agent/tmp/canary-probe-<uuid>/`; `trusted_parent` defaults to the repository root via `find_repo_root`, with an `mkdtemp` fallback outside the tree. The only location inside a phase workspace the probe accepts is that designated probe area — any other directory inside the phase workspace is rejected — and the probe owns the scratch directory and removes it when the probe ends. The probe runs with no `permission_prompt_listener`. The verdict is derived from positive evidence by `judge_probe(marker_state, facts)` in `ralph/recovery/canary_probe_judge.py`, never from transcript wording: the probe passes only when the marker proves it (written with the exact expected content); it fails as `failed_reproducing_no_work` only when the agent demonstrably ran — a `TOOL_USE`, `TOOL_RESULT`, `STREAM_DELTA` or `CHILD_*` event kind, a non-empty tool-call list, or `subagent_activity_observed` — and the marker is missing or content-mismatched; every other end (a nonzero exit without run evidence, prompt-only output, absent facts, timeout, crash or exhausted budget) is inconclusive ("probe could not run"), which is unknown and never benches.

**Typed signals.** `QuotaExhaustedError`, `MissingCredentialsError` and `BrokenAgentExitError` (prompt echo) bench on the first failure without a probe, whether connectivity is `online` or not, because their detectors read provider-originated output only. Phrase code is reachable only through the typed `ProviderText`, constructed via `provider_text` or `provider_evidence_text`; tool-call, reasoning, subagent and user-prompt content never raises them. A quota-detector kill of a session that showed liveness is a runtime end, not a bench.

**Cooldown.** A confirmed dead agent is marked unavailable with `UnavailabilityReason.DEAD_AGENT_NO_PROGRESS` through the same tracker as every other reason: 10 seconds on the first failure, doubling on each repeat, capped at the five-hour universal ceiling. A bench is a cooldown, never `Spent`; ledger thresholds (3 consecutive, 9 total unproductive attempts) are unchanged and are the only route to `Spent`. Selection never picks an agent inside its cooldown and lists agent, reason and remaining time when it waits. The cooldown survives checkpoint save and restore with its reason and attempt count and expires when the clock passes its deadline. A success calls `reset_backoff` and clears it; the first attempt after expiry is a production probe and a failure re-benches with escalation.

Optional adjudication by a model from elsewhere in the chain is not built. The canary probe alone decides every verdict.

**Termination issuer.** Every managed termination requires a typed issuer. `ProcessTerminationIssuer` names the actor that ended the session: `supervisor_idle`, `quota_detector`, `post_exit_watchdog`, `recovery_timeout`, `broken_agent_timer`, `operator`, `context_exit`, `call_timeout`, `output_limit`, `invoke_teardown`, `tool_timeout`, `mcp_shutdown`, `git_timeout`, `executor_cancel`, `test_runner`, `diagnostics`. `ManagedProcess.terminate` and `kill`, their PTY and async variants and `ProcessManager.terminate` refuse a call with no issuer, and a reason outside the permitted set raises `ValueError` before any signal is sent. `teardown_subtree` also requires the typed issuer (a plain string raises `TypeError`) and checks ownership by an `owner_label` family match, so a teardown passes only when the caller owns the subtree. A post-exit watchdog fire (`FIRE_DESCENDANT_HANG`/`FIRE_PROCESS_EXIT_HANG`) tears down with issuer `post_exit_watchdog`; any other verdict tears down as ordinary completion cleanup (`invoke_teardown`). A teardown records its outcome (`signalled`, `refused_label_mismatch`, `already_exited`) separately from the terminal reason, so teardown never masks the issuer that caused the attempt to end. The single termination authority is `ProcessManager`: `ProcessManager.terminate` ends sync and PTY sessions, and `ProcessManager.terminate_async` ends async sessions and returns only after the process record is terminal; a record that is already terminal short-circuits both and records nothing. Every `ManagedProcess`, `ManagedPtyProcess` and `ManagedAsyncProcess` `terminate`/`kill` delegates to the manager, and audit rule B9 governs signalling outside `ralph/process/`.

**Termination log.** Each attempt keeps an append-only termination log of `(issuer, source)` entries. One rule resolves it: the first runtime entry wins (`supervisor_idle`, `quota_detector`, `post_exit_watchdog`, `recovery_timeout`, `operator`); otherwise `agent_exit` wins; an empty log resolves to `unknown`.

**Attempt supervision.** `AttemptSupervisor` (`ralph.agents.invoke.attempt_supervision`) is the only invoke-layer code that ends a session. One supervisor exists per attempt, created at the attempt boundary, and it owns that attempt's single `TerminationRecorder`; the same supervisor is threaded through every completion-helper chain. Readers and detectors hand the supervisor typed observations instead of signalling the process themselves, and the supervisor maps each observation to its issuers and runs the single kill sequence: append the mapped attempt issuer, record liveness where mapped, record the terminal reason, close the handle where the observation declares a pre-signal close, terminate with the mapped process issuer, tear down the tree, and record the observed return code. The first acting observation wins: under one lock, the first observation that acts runs that sequence and every later observation is a no-op, so a losing observation can never overwrite the terminal reason or the issuer. A no-op `act` returns `None` and has no side effects; the caller returns immediately, publishes no error and writes no recorder fact, so an error published by the winning observation stays the only authoritative failure. Observations gated out as non-evidence (a provider error whose detail is not quota-kill evidence) record nothing at all.

| Observation | Attempt issuer appended | Process issuer | Terminal reason |
|---|---|---|---|
| `ProviderErrorObserved` (quota-kill evidence only) | `quota_detector` | `quota_detector` | `quota_exhausted` |
| `SilenceObserved` (watchdog fire) | the fire reason's termination issuer | the fire reason's process issuer | `conflict_inactivity` (PTY) |
| `BrokenAgentTimeoutObserved` | as the broken-agent path records today | `broken_agent_timer` | — |
| `CompletionObserved` | `agent_exit` | `invoke_teardown` | — |
| `InteractiveCompletionObserved` | — | `invoke_teardown` | `interactive_completion` |
| `TerminalStartupErrorObserved` | — | `invoke_teardown` | `terminal_startup_error` |
| `StartupFailureObserved` | — | `invoke_teardown` | — |
| `OperatorInterruptObserved` | — | `operator` | `operator_cancellation` |
| `PostExitHangObserved` | `post_exit_watchdog` | `post_exit_watchdog` | — |

`cleanup` is the supervisor's other operation: generic teardown of a tree whose end was already settled, or unwinding after an exception. It takes a process issuer and a teardown function, optionally terminates the handle first (`terminate=True`), never writes the issuer log, never counts as an acting observation, and is never blocked by an earlier `act`. Completion-verdict reaps, the PTY terminal-completion reap and runner unwinding all go through `cleanup`.

**Attempt outcome kinds.** `AttemptOutcome` is a closed sum of six kinds, and each kind has fixed accounting effects.

- `completed` — the attempt produced its artifact. It resets the agent's backoff and does not count toward agent state.
- `retryable_failure` — an ordinary failure. It counts toward the ledger and is the only kind that retries the same agent.
- `runtime_ended` — a runtime, detector or operator ended the session. It never benches and never counts toward the ledger, and it carries the issuer.
- `task_or_environment_failure` — the outcome already exists, or the chain's agents failed identically. It never benches and never counts toward the ledger.
- `agent_dead` — the agent is benched, with the persisted verdict attached.
- `unknown` — the evidence does not support a verdict. It keeps ordinary ledger accounting and never benches.

**One finalization per attempt.** `RecoveryController.finalize_attempt` is the only place that judges, probes, benches, charges the ledger, resets a success, records peer facts and writes the verdict record. Every route calls it: the classifier path, the confirmed-dead-agent path, both reducer routes and the attempt runner. The in-session retry finalizes each failed attempt before the next same-agent attempt starts, and the finalization is memoised by an attempt key, so one attempt is judged exactly once even when several routes observe it.

**Peer failures.** The peer-failure fact compares a signature of `(artifact_valid, termination issuer, exit status, workspace changed, event kinds)` against the last failure each agent in the same phase recorded since that phase last completed. Peers failing identically is a task-or-environment failure, never a bench.

**Persisted verdict.** Every verdict is stored in the checkpoint with the facts, attribution, probe outcome, termination record and return-code discrepancy flag it used, so a bench followed by a later success can be counted as a false positive. The record carries `schema_version: 2` and the full termination log; a version-1 record still loads.

**Corpus.** `tests/recovery/dead_agent_corpus/` holds labelled real sessions (one positive, the agy session; negatives for the codex quote kill, the opencode subagent kill, the claude live-session end, the claude stop-hook turn end with pending subagents, the claude probe trust dialog, the opencode post-exit watchdog kill, transient reconnects, slow productive sessions, sessions that wrote files, outcomes that already exist, and peers failing identically). `tests/recovery/test_dead_agent_corpus_replay.py` replays every fixture through the recovery controller and reducer, requires zero false benches and prints `false_benches=X/N`.

**Adding an incident fixture.** Copy the closest JSON file in `tests/recovery/dead_agent_corpus/`, set a new `id`, `label` (`positive` only for a confirmed dead agent), `agent`, `transport`, `incident`, the captured raw lines, tri-state `workspace_changed` and `workspace_changed_by_tool`, `termination` (issuer and both return codes), `liveness_observed`, `subagent_activity_observed`, and the `expected_attribution`, `expected_verdict` and `expected_bench` values. Add its id to the expected sets in `tests/recovery/dead_agent_corpus/test_corpus_fixtures.py`. The replay test picks the file up without further changes.

**False-bench feedback.** A bench is a bet, so it is measured. Each bench opens a candidate keyed by the agent and a stable, non-secret fingerprint of the agent's configuration: the agent name, the executable name, the model, the model flag, the parser and the transport. The fingerprint never reads credentials or the rest of the command line. Only a later success under the same fingerprint closes the candidate and increments the count, so a configuration change cannot silently absorb a false bench. The run-time report prints `False benches: N (agent@phase [fingerprint], …)`. Each counted event keeps the verdict record and the session reference it came from. `false_bench_fixture_draft` renders a counted event as a corpus-fixture draft; a reviewer fills in the captured raw lines from the referenced session and commits the fixture. This record is a metric: selection never reads it, and it is not a second availability store.

**Runner and entry points.** `ralph.pipeline.attempt_runner.run_agent_attempt` is the single entry point for invoking an agent. It owns the recovery construction seam, so a pipeline receives a configured controller instead of assembling one, and it returns the attempt outcome, the facts and the ordered per-attempt outcomes. The nine registered entry points are `main_phase_loop`, `parallel_worker`, `integration_continuation`, `conflict_resolution`, `commit_generation`, `smoke`, `project_policy`, `session_runtime` and `canary_probe`. The canary probe runs in probe mode: it records facts and the issuer and reports the outcome, but it runs no nested probe, benches nothing and charges no ledger.

## Boundaries

The detector uses only observable evidence from a single invocation. It does not diagnose a provider outage or infer that a model request was accepted.

The grace timer applies while the process remains live and silent. A completed process with unambiguous no-output, harness-only, prompt-echo, or credential-failure evidence falls over immediately instead of entering a resumable same-agent retry. The live-process timer is intentionally independent of normal idle-watchdog thresholds so retry policy can respond quickly when an agent cannot begin useful work.

The readers still hold the process handle for I/O; ending the session is the supervisor's act, and audit rule B9 forbids any other invoke-layer signalling. Readers losing the handle entirely, so a detector structurally cannot kill, is a tracked deferral (refactor item 2).

Parsers do not yet tag every normalised event with a content channel; tagging is deferred with the provider-channel feed to detectors (refactor item 3). In the meantime the boundary is enforced by type and by tooling: quota detection reaches phrase code only through `ProviderText` (both constructors), and audit rule B8 in `ralph/testing/repo_structure_boundary_rules.py` rejects `ralph.process*` imports and `ManagedProcess` references in the quota-detection and content-channel modules, paired with matrix scenario AC4 (`quota_words_are_data`).

Audit rule B9 in `ralph/testing/repo_structure_boundary_rules.py` enforces the supervision boundary. Inside `ralph/agents/invoke/` and `ralph/agents/idle_watchdog/`, only `ralph/agents/invoke/attempt_supervision/attempt_supervisor.py` may make a signalling call; outside `ralph/process/` and `ralph/testing/`, any handle-level `.terminate(`/`.kill(` method call is a violation, with or without a typed `issuer`, and termination must route through `ProcessManager.terminate`/`terminate_async` (calls on a `get_process_manager()`/`ProcessManager` receiver and the `ralph/process/unmanaged_terminate.py` helpers for processes the manager does not own pass). `os` and `signal` module calls remain governed by rule B4, including its dispatcher allowlist and zero-signal liveness probes. Inside `ralph/process/` the sole signalling entry point is the manager — `ProcessManager.terminate`/`terminate_async` — because every handle-level `terminate`/`kill` delegates to it.

Facts are assembled in one place and the log line renders from facts only. Per-CLI fact completeness is asserted by `tests/agents/invoke/test_cli_attempt_fact_completeness.py`, which replays the committed captures through the real reader seam; the gaps it records (no kimi capture, no recorded opencode subagent frame, several parsers not unwrapping tool frames, no subagent frames in most captures) are tracked as deferrals in PROMPT.md's refactor status.

A counted false bench reaches the corpus through the reviewed fixture-draft step, not automatically.

The spent-agent set stays inside `recovery/controller.py`; no other module may write it.

A prompt echo is not a generic similarity check. The entire trimmed prompt must be present in the emitted line, which preserves short genuine responses that happen to share words with the prompt.

Normal output mixed with an echoed prompt remains meaningful. In that case Ralph follows the regular completion and watchdog contracts rather than declaring the agent broken.

See [Recovery](../sphinx/recovery.md) for chain order and cooldown behavior, and [Configuration](../sphinx/configuration.md) for the `[general]` keys that bound agent retries and the removed-settings migration.
