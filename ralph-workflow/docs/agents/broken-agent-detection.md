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

A broken-agent exit skips same-agent retries, so that first failure advances to the next available agent when the chain has one; other unproductive attempts (a missing artifact, for example) retry the same agent until the consecutive limit demotes it. A sole-agent chain has nowhere to fall over to, so reaching the limit fails the phase with a clear diagnostic message instead of retrying indefinitely.

## Operator Log Lines

A demotion is logged once, when it happens:

```
Agent '<agent>' demoted in phase '<phase>' (consecutive=<n>, total=<n>, reason=<reason>)
```

Agent selection reports every candidate it skipped and why. The skip reasons are `cooldown` (the agent is still cooling down, reported with the milliseconds remaining and, when known, the cooldown reason), `unavailable` (the agent is marked unavailable by a failed invocation), `spent` (the agent is permanently demoted for the phase), and `lower_priority` (the agent was eligible, but a higher-priority candidate was selected).

## Operator Response

For a fast no-output subprocess exit, start with the `BrokenAgentExitError` exit code and stderr text. They distinguish a hard process crash from a clean no-output completion, but they do not by themselves prove why a provider failed. Then check the selected agent's credentials, command configuration, extension setup, and provider availability. A prompt-echo diagnosis also indicates that the harness started but the model did not provide meaningful output.

After correcting credentials or provider configuration, allow the unavailable-agent cooldown to expire or restart the run using the configured recovery workflow.

## Boundaries

The detector uses only observable evidence from a single invocation. It does not diagnose a provider outage or infer that a model request was accepted.

The grace timer applies while the process remains live and silent. A completed process with unambiguous no-output, harness-only, prompt-echo, or credential-failure evidence falls over immediately instead of entering a resumable same-agent retry. The live-process timer is intentionally independent of normal idle-watchdog thresholds so retry policy can respond quickly when an agent cannot begin useful work.

A prompt echo is not a generic similarity check. The entire trimmed prompt must be present in the emitted line, which preserves short genuine responses that happen to share words with the prompt.

Normal output mixed with an echoed prompt remains meaningful. In that case Ralph follows the regular completion and watchdog contracts rather than declaring the agent broken.

See [Recovery](../sphinx/recovery.md) for chain order and cooldown behavior, and [Configuration](../sphinx/configuration.md) for the `[general]` keys that bound agent retries and the removed-settings migration.
