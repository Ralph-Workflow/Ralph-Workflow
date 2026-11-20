# Broken-Agent Detection

Ralph treats an agent as broken when circumstantial evidence shows that it cannot produce meaningful LLM output. The recovery action is fallover to the next configured agent, rather than retrying the same agent.

## Detection

`BROKEN_AGENT_OUTPUT_GRACE_SECONDS` is 12 seconds. It is intentionally shorter than the 360-second `NO_OUTPUT_AT_START_SECONDS` watchdog threshold.

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

That `broken_agent` cooldown is a reason in the one cooldown system; its duration, growth and reset are defined in [Agent cooldown: the sole cooldown system](cooldown-invariant.md). Recovery publishes the normal fallover event and advances the agent chain.

## Agent failure limits

Exactly one component decides that an agent has failed too many times: the agent failure ledger (`AgentFailureLedger`). The recovery controller, the pipeline reducer, and conflict-resolution selection are clients of that ledger — they record attempt facts into it, or apply the verdict it returns. No second tracker, counter, or cap may decide that an agent retries, cools down, or runs next; a seam that cannot reach the ledger fails its phase closed instead of granting another attempt. The ledger's only result is an `unproductive_limit` cooldown; the limits (`agent_max_consecutive_unproductive`, `agent_max_total_unproductive`), the cooldown durations and the wait when every agent is on cooldown are defined in [Agent cooldown: the sole cooldown system](cooldown-invariant.md).

A broken-agent exit skips same-agent retries, so that first failure advances to the next available agent when the chain has one; other unproductive attempts (a missing artifact, for example) retry the same agent until the consecutive limit starts its cooldown.

## Operator Log Lines

Every cooldown is logged once, when it starts, as a `cooldown_decision` line:

```
cooldown_decision phase=<phase> agent=<agent> reason=<reason> duration_ms=<n> expiry_ms=<ms> ...
```

When an attempt leaves the agent on cooldown, its in-effect retries stop with:

```
Stopping agent '<agent>' retries: agent is on cooldown until <ms> ms (reason=<reason>); it is retried once the cooldown expires.
```

Agent selection reports every candidate it skipped and why. The skip reasons are `cooldown` (the agent is still cooling down, reported with the milliseconds remaining and, when known, the cooldown reason), `excluded_from_this_selection` (the caller excluded the agent for this one selection, for example a resolver already invoked in the current round), and `lower_priority` (the agent was eligible, but a higher-priority candidate was selected).

## Operator Response

For a fast no-output subprocess exit, start with the `BrokenAgentExitError` exit code and stderr text. They distinguish a hard process crash from a clean no-output completion, but they do not by themselves prove why a provider failed. Then check the selected agent's credentials, command configuration, extension setup, and provider availability. A prompt-echo diagnosis also indicates that the harness started but the model did not provide meaningful output.

After correcting credentials or provider configuration, allow the agent's cooldown to expire or restart the run using the configured recovery workflow.
## Dead-Agent Verdict

An attempt that ends without a valid artifact is judged in four recorded layers. Only the verdict layer starts a dead-agent cooldown, and "unknown" is a first-class answer that never starts one.

**Facts.** Each attempt records `AttemptFacts`: event kinds in order, tool calls, workspace change as true, false or unknown (never collapsed), whether the artifact is valid, time to the terminal state, exit status, liveness observations (events in the window, subagent store updates), and a termination record. The termination record holds the issuer (`agent_exit`, `supervisor_idle`, `quota_detector`, `post_exit_watchdog`, `recovery_timeout`, `operator`, `unknown`), the return code the process manager observed, and the return code reported upward. When the two return codes differ the record is flagged. A signal-shaped return code (negative, or 129-159) or a missing return code without an attributed issuer is `unknown`, never `agent_exit`. Raw message text is for humans and audit only.

**Attribution.** Facts classify the end as `agent`, `runtime`, `task_or_environment` or `unknown`. A runtime-issued end (`quota_detector`, `supervisor_idle`, `recovery_timeout`, `post_exit_watchdog`, `operator`) or an unattributed end of a live session is labelled with its cause (`runtime_kill_quota_misread`, `runtime_kill_supervisor_idle`, `runtime_kill_post_exit_watchdog`, `runtime_kill_unattributed`, and so on) and never counts toward a cooldown. A valid outcome, workspace writes, mid-run activity (tool calls, subagent updates) and peers failing identically point to the task or environment. An interactive turn end with subagents still pending — a stop-hook sentinel firing while the session is live and the artifact is missing — records no issuer, is attributed `runtime_kill_unattributed`, resolves `not_agent_fault` and never starts a cooldown or counts toward the ledger, even though tool calls and subagent activity were observed. Attribution rules only clear an agent; they never convict one.

**Probe.** When attribution leaves the agent as a suspect, a canary probe runs the same CLI, model, credentials and environment on a fixed trivial task — write one marker file with exact content — with a mechanically checked result and a 30-second timeout. At most three probes run per pipeline run. The probe launches its scratch workspace under `<trusted_parent>/.agent/tmp/canary-probe-<uuid>/`; `trusted_parent` defaults to the repository root via `find_repo_root`. A probe that resolves no pre-trusted parent creates no scratch directory and fails closed with the reason `no_pretrusted_workspace`. The only location inside a phase workspace the probe accepts is that designated probe area — any other directory inside the phase workspace is rejected — and the probe owns the scratch directory and removes it once the probe's own session has ended; a probe that times out keeps its scratch directory while its session may own it and removes it when that session ends. The probe launches only a CLI whose resolved command carries a headless selection (the configuration's own print flag, `-p` or `--print`); a transport that carries no headless selection is not probe-safe and fails closed with the reason `transport_not_probe_safe`. The probe runs with no `permission_prompt_listener`. The verdict is derived from positive evidence by `judge_probe(marker_state, facts)` in `ralph/recovery/canary_probe_judge.py`, never from transcript wording: the probe passes only when the marker proves it (written with the exact expected content), and the marker is read on every end, so a probe that wrote it is `passed` even when the CLI then exits nonzero or the timeout elapses; it fails as `failed_reproducing_no_work` only when the agent demonstrably ran — a `TOOL_USE`, `TOOL_RESULT` or `CHILD_*` event kind, a non-empty tool-call list, or `subagent_activity_observed` — and the marker is missing or content-mismatched. A `STREAM_DELTA` is activity, not task evidence: a CLI that printed a dialog and exited 0 is not convicted. Every other end (a nonzero exit without run evidence, prompt-only output, absent facts, timeout, crash or exhausted budget) is inconclusive ("probe could not run"), which is unknown and never starts a cooldown; it reports the class `probe_could_not_run` and the reason that produced it (`invocation_error`, `probe_timeout`, `probe_internal_error`, `budget_exhausted`, `no_pretrusted_workspace`, `transport_not_probe_safe`, or `probe_terminated_by_runtime:<issuer>[:rc=N]`).

**Typed signals.** `QuotaExhaustedError`, `MissingCredentialsError` and `BrokenAgentExitError` (prompt echo) start a cooldown on the first failure without a probe, whether connectivity is `online` or not, because their detectors read provider-originated output only. Phrase code is reachable only through the typed `ProviderText`, constructed via `provider_text` or `provider_evidence_text`; tool-call, reasoning, subagent and user-prompt content never raises them. A quota-detector kill of a session that showed liveness is a runtime end, not a cooldown.

**Cooldown.** A confirmed dead agent goes on a `dead_agent_no_progress` cooldown in the same tracker as every other reason; an agent is either on cooldown or available. Its duration, growth, persistence and reset are defined in [Agent cooldown: the sole cooldown system](cooldown-invariant.md).

Optional adjudication by a model from elsewhere in the chain is not built. Typed provider signals decide their own first-failure cooldowns; the canary adjudicates suspect attempts.

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

`cleanup` is the supervisor's other operation: generic teardown of a tree whose end was already settled, or unwinding after an exception. It takes a process issuer and uses the teardown function bound at construction, optionally terminates the handle first (`terminate=True`), never writes the issuer log, never counts as an acting observation, and is never blocked by an earlier `act`. Completion-verdict reaps, the PTY terminal-completion reap and runner unwinding all go through `cleanup`.

**Attempt outcome kinds.** `AttemptOutcome` is a closed sum of six kinds, and each kind has fixed accounting effects.

- `completed` — the attempt produced its artifact. It resets the agent's backoff and does not count toward agent state.
- `retryable_failure` — an ordinary failure. It counts toward the ledger and is the only kind that retries the same agent.
- `runtime_ended` — a runtime, detector or operator ended the session. It never starts a cooldown and never counts toward the ledger, and it carries the issuer.
- `task_or_environment_failure` — the outcome already exists, or the chain's agents failed identically. It never starts a cooldown and never counts toward the ledger.
- `agent_dead` — the agent gets a `dead_agent_no_progress` cooldown, with the persisted verdict attached.
- `unknown` — the evidence does not support a verdict. It keeps ordinary ledger accounting and never starts a dead-agent cooldown.

**One finalization per attempt.** `RecoveryController.finalize_attempt` is the only place that judges, probes, starts dead-agent cooldowns, charges the ledger, resets a success, records peer facts and writes the verdict record. Every route calls it: the classifier path, the confirmed-dead-agent path, both reducer routes and the attempt runner. The in-session retry finalizes each failed attempt before the next same-agent attempt starts, and the finalization is memoised by an attempt key, so one attempt is judged exactly once even when several routes observe it.

**Peer failures.** The peer-failure fact compares a signature of `(artifact_valid, termination issuer, exit status, workspace changed, event kinds)` against the last failure each agent in the same phase recorded since that phase last completed. Peers failing identically is a task-or-environment failure, never a cooldown.

**Persisted verdict.** Every verdict is stored in the checkpoint with the facts, attribution, probe outcome, termination record and return-code discrepancy flag it used, so a dead-agent cooldown followed by a later success can be counted as a false positive. The record carries `schema_version: 3` with the probe reason and its elapsed time, and the full termination log; a version-1 or version-2 record still loads.

**Corpus.** `tests/recovery/dead_agent_corpus/` holds labelled real sessions (one positive, the agy session; negatives for the codex quote kill, the opencode subagent kill, the claude live-session end, the claude stop-hook turn end with pending subagents, the claude probe trust dialog, the opencode post-exit watchdog kill, transient reconnects, slow productive sessions, sessions that wrote files, outcomes that already exist, and peers failing identically). `tests/recovery/test_dead_agent_corpus_replay.py` replays every fixture through the recovery controller and reducer, requires zero false cooldowns and prints its false-cooldown rate.

**Adding an incident fixture.** Copy the closest JSON file in `tests/recovery/dead_agent_corpus/`, set a new `id`, `label` (`positive` only for a confirmed dead agent), `agent`, `transport`, `incident`, the captured raw lines (`captured_lines`), tri-state `workspace_changed` and `workspace_changed_by_tool`, `artifact_valid`, `elapsed_to_terminal_ms`, `exit_status`, `termination` (issuer and both return codes), `liveness_observed`, `subagent_activity_observed`, and the `expected_attribution`, `expected_verdict` and `expected_cooldown` values. Two further keys carry the signals the derivation reads: `outcome_exists` (`true` when the phase outcome already exists, which is the satisfied phase outcome `artifact_valid`) and `peers_fail_identically` (`true`, with the `peers` list naming the agents that failed identically, which is the peer signal the attribution reads). Add its id to the expected sets in `tests/recovery/dead_agent_corpus/test_corpus_fixtures.py`. The replay test picks the file up without further changes.

**False-cooldown feedback.** A dead-agent cooldown is a bet, so it is measured. Each one opens a candidate keyed by the agent and a stable, non-secret fingerprint of the agent's configuration: the agent name, the executable name, the model, the model flag, the parser and the transport. The fingerprint never reads credentials or the rest of the command line. Only a later success under the same fingerprint closes the candidate and increments the count, so a configuration change cannot silently absorb a false cooldown. The run-time report prints `False cooldowns: N (agent@phase [fingerprint], …)`. Each counted event keeps the verdict record and the session reference it came from. `false_cooldown_fixture_draft` renders a counted event as a corpus-fixture draft; a reviewer fills in the captured raw lines from the referenced session and commits the fixture. This record is a metric: selection never reads it, and it is not a second availability store.

**Runner and entry points.** `ralph.pipeline.attempt_runner.run_agent_attempt` is the single entry point for invoking an agent. It owns the recovery construction seam, so a pipeline receives a configured controller instead of assembling one, and it returns the attempt outcome, the facts and the ordered per-attempt outcomes. The nine registered entry points are `main_phase_loop`, `parallel_worker`, `integration_continuation`, `conflict_resolution`, `commit_generation`, `smoke`, `project_policy`, `session_runtime` and `canary_probe`. Every production dispatch goes through the registered runner of its entry point; the repo-structure audit rejects a `run_agent_attempt` call outside the nine registered wrappers. The recovery machinery has exactly one construction seam — `ralph.recovery.controller_factory.build_recovery_controller` — and the failure ledger has exactly one owner, so the reducer charges the ledger through the controller instead of writing it directly. The canary probe runs in probe mode: it records facts and the issuer and reports the outcome, but it runs no nested probe, starts no cooldown and charges no ledger.

## Boundaries

The detector uses only observable evidence from a single invocation. It does not diagnose a provider outage or infer that a model request was accepted.

The grace timer applies while the process remains live and silent. A completed process with unambiguous no-output, harness-only, prompt-echo, or credential-failure evidence falls over immediately instead of entering a resumable same-agent retry. The live-process timer is intentionally independent of normal idle-watchdog thresholds so retry policy can respond quickly when an agent cannot begin useful work.

Ending a session is the supervisor's act: every teardown is bound to the attempt's `AttemptSupervisor`, which receives its teardown function by constructor injection and acts on the first acting observation only. The readers keep the process handle for I/O and `poll()`; a detector structurally cannot kill, because no invoke-layer module outside `attempt_supervision/` references `teardown_subtree` at runtime — audit rule B9 forbids any other invoke-layer signalling and audit clause B9b enforces the teardown reference boundary, paired with matrix AC3/AC15. A full read-only process view for readers remains a tracked deferral (refactor item 2).

Parsers tag every normalised event with a content channel, and readers pass the parser-tagged channel via `line_channel(parser_key, raw_line)` and `provider_quota_line_evidence(line, channel=...)`; `channel_of` remains only as the fallback for unparsed lines. Quota detection reaches phrase code only through `ProviderText` (both constructors), and audit rule B8 in `ralph/testing/repo_structure_boundary_rules.py` rejects `ralph.process*` imports and `ManagedProcess` references in the quota-detection and content-channel modules, paired with matrix scenario AC4 (`quota_words_are_data`).

Audit rule B9 in `ralph/testing/repo_structure_boundary_rules.py` enforces the supervision boundary. Inside `ralph/agents/invoke/` and `ralph/agents/idle_watchdog/`, only `ralph/agents/invoke/attempt_supervision/attempt_supervisor.py` may make a signalling call; outside `ralph/process/` and `ralph/testing/`, any handle-level `.terminate(`/`.kill(` method call is a violation, with or without a typed `issuer`, and termination must route through `ProcessManager.terminate`/`terminate_async` (calls on a `get_process_manager()`/`ProcessManager` receiver and the `ralph/process/unmanaged_terminate.py` helpers for processes the manager does not own pass). `os` and `signal` module calls remain governed by rule B4, including its dispatcher allowlist and zero-signal liveness probes. Inside `ralph/process/` the sole signalling entry point is the manager — `ProcessManager.terminate`/`terminate_async` — because every handle-level `terminate`/`kill` delegates to it.

Facts are assembled in one place and the log line renders from facts only; `AttemptFacts.typed_signal` is the single carrier — an in-memory fact filled in one place, read by every route, and the source of the persisted top-level `typed_signal` slot. Per-CLI fact completeness is asserted by `tests/agents/invoke/test_cli_attempt_fact_completeness.py`, which replays the committed captures through the real reader seam; the remaining gaps it records are `subagent_activity_observed` rows for claude, claude_interactive, codex, cursor, gemini, generic, kimi, nanocoder and pi (their committed captures carry no subagent frame on the wire), tracked as deferrals in the refactor status. Healthy generic and nanocoder captures populate `tool_calls`; no `tool_calls` gap row remains.

A counted false cooldown reaches the corpus through the reviewed fixture-draft step, not automatically.

Only `recovery/controller.py`, `recovery/agent_failure_ledger.py` and `recovery/agent_unavailability_tracker.py` may start a cooldown (`mark_unavailable`); no other module writes agent availability.

A prompt echo is not a generic similarity check. The entire trimmed prompt must be present in the emitted line, which preserves short genuine responses that happen to share words with the prompt.

Normal output mixed with an echoed prompt remains meaningful. In that case Ralph follows the regular completion and watchdog contracts rather than declaring the agent broken.

See [Recovery](../sphinx/recovery.md) for chain order and cooldown behavior, and [Configuration](../sphinx/configuration.md) for the `[general]` keys that bound agent retries and the removed-settings migration.
