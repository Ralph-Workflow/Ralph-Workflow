---
orphan: true
---

# Agents

Ralph Workflow can supervise multiple coding agents, but the contract stays the same: the workflow is built for **unattended** orchestration that still comes back reviewable in the morning.

## Supported agents

Ralph Workflow currently supports **Claude**, **Codex**, **OpenCode**, **Nanocoder**, and **Google Anti Gravity** as orchestration targets. Each runs under the same unattended workflow contract described on this page. For help choosing, see [Which Agent Should I Start With?](which-agent-should-i-start-with.md).

> See `ralph/skills/_agent_paths.py` for the canonical mapping of every supported agent's user-global skill-discovery root.

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

## Dedicated parallel worker bootstrap

When Ralph Workflow fans out parallel workers for a multi-unit execution, each worker enters through a dedicated bootstrap path that short-circuits the shared pipeline startup loop.

### What each worker receives

Each parallel worker gets its own isolated execution context:

- **Work-unit manifest** — serialized at `.agent/workers/<unit_id>/worker-manifest.json` before launch, containing the unit description, allowed directories, phase, drain, and the parent run's config path and CLI overrides
- **Worker-local prompt dump** — rendered prompt written to `.agent/workers/<unit_id>/tmp/<phase>_prompt.md` instead of the shared `.agent/tmp/` location
- **Worker-local checkpoint** — saved to `.agent/workers/<unit_id>/tmp/checkpoint.json` instead of `.agent/checkpoint.json`
- **Worker-local system prompt and current-prompt mirror** — materialized under the same worker namespace, keeping the worker's view of PROMPT.md and system prompt isolated from other workers
- **Worker-local multimodal sidecar** — handoff metadata written to `.agent/workers/<unit_id>/tmp/<phase>_multimodal_handoff.json`

### Why isolation matters

The old bootstrap path launched workers as generic `python -m ralph` invocations that entered the shared pipeline loop and competed for singleton runtime files. The dedicated bootstrap path bypasses that loop entirely and threads the work-unit context through the manifest so each worker operates on its own state.

Post-fanout verification remains serialized — Ralph Workflow waits for all workers to finish before running the single verification step, but the workers themselves execute in parallel with no shared state to corrupt.

### Bootstrap entry point

Workers launched via fan-out receive the manifest path through the hidden `--parallel-worker-manifest` CLI option. The worker runtime loads the manifest, reconstructs the work-unit context, materializes the prompt for the unit, and executes the phase without re-entering the outer pipeline loop.

## Related pages

- [Developer Internals](developer-internals.md)
- [MCP Architecture](mcp-architecture.md)
- [Artifacts](artifacts.md)
- [Transcript and Display Reference](transcript.md)
