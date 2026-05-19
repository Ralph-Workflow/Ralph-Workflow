# Agents

Ralph Workflow can supervise multiple coding agents, but the contract stays the same: the workflow is built for **unattended** orchestration that still comes back reviewable in the morning.

## What this page is for

This page explains how Ralph Workflow orchestrates agent sessions, what completion means, and why interactive and headless transports make different tradeoffs.

## The unattended orchestration contract

Ralph Workflow does not treat an agent transcript as proof that the work is done. It supervises each session, orchestrates the configured phases, and looks for concrete completion evidence before handing control back.

That evidence comes from:

- **artifact** output that shows what was produced for the phase
- explicit tool or MCP signals such as `declare_complete`
- verification and review steps that confirm the handoff is not just a confident draft

If an agent exits **without completing** the phase, Ralph Workflow treats that as **incomplete** work rather than silently calling it done. The session can be resumed, retried, or routed through the next recovery path depending on the configured policy.

## Interactive vs headless modes

Interactive transports give Ralph Workflow better streaming **observability** into what the agent is doing during a live session. Headless transports can be simpler to automate, but the tradeoff is less step-by-step visibility while the run is in flight.

That tradeoff matters most when you want stronger supervision of a long-running interactive coding session. Ralph Workflow can still manage either mode, but the operational visibility differs.

## Completion and parser behavior

Completion is evaluated from durable evidence, not from a conversational vibe. Parsers may produce **bounded summaries** of what happened, but they do not preserve every multimodal parser output as first-class artifacts in the final event stream.

In practice, Ralph Workflow expects either:

- phase artifacts that show the result
- an explicit `declare_complete` call
- or a recovery path when the session ends before either condition is met

## Resolved capability delivery

Multimodal delivery is decided per session through `ResolvedCapabilityProfile`, which acts as the pre-computed, session-owned contract for how each modality is delivered to the active agent transport.

That keeps media, artifacts, and tool output aligned with the capabilities of the current session instead of assuming one fixed behavior for every provider.

## Related pages

- [Developer Internals](developer-internals.md)
- [MCP Architecture](mcp-architecture.md)
- [Artifacts](artifacts.md)
- [Transcript and Display Reference](transcript.md)
