# Broken-Agent Detection

Ralph treats an agent as broken when circumstantial evidence shows that it cannot produce meaningful LLM output. The recovery action is fallover to the next configured agent, rather than retrying the same agent.

## Detection

`BROKEN_AGENT_OUTPUT_GRACE_SECONDS` is 30 seconds. It is intentionally shorter than the 120-second `NO_OUTPUT_AT_START_SECONDS` watchdog threshold.

Both subprocess and PTY line readers enforce the grace window while the process is still alive. If no meaningful output appears by the deadline, the reader terminates the process tree and raises `BrokenAgentExitError` with `reason="no_output"`.

A clean exit is also checked before the normal resumable-exit path. If the elapsed runtime reached the grace window and the bounded output is empty, the completion gate raises the same error.

## Non-LLM Activity

`reason="no_llm_activity"` identifies an invocation that exceeded the grace window without meaningful LLM output but did produce classified non-LLM activity, such as lifecycle frames, harness echoes, progress reports, errors, or tool events. This differs from `no_output`, where no classified output was seen, and `prompt_echo`, where every nonblank line was a deterministic echo of the input prompt.

The completion check uses invocation age rather than time since the most recent activity, so continual lifecycle output cannot indefinitely hide this condition. Treat this reason as a credentials or provider-availability problem and follow the same fallover response as the other broken-agent reasons.

## Prompt Echoes

Harness output is not evidence of LLM work when it deterministically echoes the input prompt. The transport-neutral `_is_prompt_echo_line` helper recognizes only nonblank lines that either equal the complete stripped prompt or contain that complete prompt verbatim.

The inverse substring case is deliberately excluded. For example, `plan` is valid output even if the prompt contains the word `plan`.

Readers mark matching lines as harness echoes, so `IdleWatchdog.has_meaningful_output()` remains false. The completion gate raises `BrokenAgentExitError(reason="prompt_echo")` when every nonblank output line is a prompt echo.

## Recovery

`BrokenAgentExitError` has `skip_same_agent_retries=True` and includes a credential/provider hint for operators. `FailureClassifier` maps it to `UnavailabilityReason.BROKEN_AGENT`, marks it unavailable, and resets its session.

The default `BROKEN_AGENT` backoff starts at 5 seconds and caps at 60 seconds. Recovery publishes the normal fallover event and advances the agent chain.

## Operator Response

Check the selected agent's credentials and provider availability first. A prompt-echo diagnosis also indicates that the harness started but the model did not provide meaningful output.

After correcting credentials or provider configuration, allow the unavailable-agent cooldown to expire or restart the run using the configured recovery workflow.

## Boundaries

The detector uses only observable evidence from a single invocation. It does not diagnose a provider outage or infer that a model request was accepted.

The grace timer applies while the process remains live and silent. It is intentionally independent of normal idle-watchdog thresholds so retry policy can respond quickly when an agent cannot begin useful work.

A prompt echo is not a generic similarity check. The entire trimmed prompt must be present in the emitted line, which preserves short genuine responses that happen to share words with the prompt.

Normal output mixed with an echoed prompt remains meaningful. In that case Ralph follows the regular completion and watchdog contracts rather than declaring the agent broken.

See the recovery configuration documentation for chain order and cooldown behavior.
