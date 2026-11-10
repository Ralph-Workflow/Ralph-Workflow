# Required Verification (before PR/completion)

> **This file is a pointer, not a second contract.** The canonical
> verification contract is
> [`docs/agents/verification.md`](../../../docs/agents/verification.md) at the
> repository root.

The repository has ONE verification contract: the root
[`docs/agents/verification.md`](../../../docs/agents/verification.md). It owns
the canonical command (`make verify`), the no-unrelated-failure-exemption rule,
the pinned combined test budget and its import-time invariant table, the
non-circumvention table, the verify step registry (including the instruction
integrity, dependency integrity, secret scan and test integrity gates), and the
audit-backed contracts — terminal escape containment, terminal state
restoration, and the MCP timeout contract.

Read that file and change that file. This copy used to hold an older variant of
the invariant list and a link into a policy pack that no longer existed, which
is exactly how one contract becomes two divergent ones.

Two neighbouring surfaces answer different questions and are not duplicates of
it:

- The **policy layer** — what a project must do, and which gate enforces it —
  lives in the root policy pack:
  [`docs/ralph-workflow-policy/verification-policy.md`](../../../docs/ralph-workflow-policy/verification-policy.md).
- The **test-design rules** live in the
  [Testing Guide](../../../docs/agents/testing-guide.md); the
  [Remote sync](remote-sync.md) contract records the integration fast-forward
  behaviour; the
  [artifact submission contract](artifact-submission-contract.md) records what
  a phase must hand over.
