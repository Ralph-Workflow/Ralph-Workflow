# Architecture Docs

The runtime architecture for the maintained Python package lives under
[`ralph-workflow/docs/sphinx/developer-internals.md`](../../ralph-workflow/docs/sphinx/developer-internals.md).
For the interrupt architecture ADR see
[`ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md`](../../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md).

Agent recovery has one owner: `RecoveryController` owns agent cooldowns,
failure-budget charging, backoff, candidate selection, and
wait/termination. Agent-invoking paths report every failed or
unproductive attempt through it, and a failure may be unpenalised only after
proven progress. Round caps come from configured limits; phases keep no local
skip/backoff/cooldown collection. The audited path list (each marked already
compliant or fixed) and the three tested non-agent exceptions live in
[`ralph-workflow/docs/agents/cooldown-invariant.md`](../../ralph-workflow/docs/agents/cooldown-invariant.md).
The short operator summary is in
[`docs/agents/architecture.md`](../agents/architecture.md#shared-recovery-ownership).

The retired Rust implementation's architecture material is quarantined
under [`../legacy-rust/`](../legacy-rust/README.md) and is not a source
of current behavior.
