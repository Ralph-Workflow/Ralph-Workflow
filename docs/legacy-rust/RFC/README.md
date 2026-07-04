# Ralph RFCs (Request for Comments)

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This directory contains design proposals and incident writeups for significant changes to Ralph.

RFCs are primarily kept for historical context. Even when an RFC is marked "Implemented", the codebase may have evolved since it was written.

For the most up-to-date architecture documentation, prefer:

- `../architecture/pipeline-lifecycle.md`
- `../architecture/event-loop-and-reducers.md`
- `../architecture/effect-system.md`
- `../architecture/streaming-and-parsers.md`

## What is an RFC?

An RFC is a design document that describes a proposed change to Ralph. RFCs are used for:

- Major new features
- Significant architectural changes
- Breaking changes
- Cross-cutting concerns that affect multiple components

## RFC Process

1. **Draft**: Create a new RFC document using the template below
2. **Review**: Share with maintainers and community for feedback
3. **Accepted/Rejected**: Decision made based on discussion
4. **Implemented**: Code changes made according to the RFC
5. **Completed**: RFC marked as implemented

## RFC Status

| Status | Meaning |
|--------|---------|
| Draft | Under development, not yet ready for review |
| In Progress | Implementation has started, some features complete |
| Review | Open for community feedback |
| Accepted | Approved for implementation |
| Rejected | Not moving forward |
| Production Ready ✅ | Implemented and treated as stable reference quality |
| Implemented | Code changes complete |
| Superseded | Replaced by a newer RFC |

## Current RFCs

| RFC | Title | Status |
|-----|-------|--------|
| [RFC-001](./RFC-001-enhanced-opencode-integration.md) | Enhanced OpenCode Integration | Draft |
| [RFC-002](./RFC-002-ux-improvements.md) | Developer Experience Improvements | In Progress |
| [RFC-003](./RFC-003-streaming-architecture-hardening.md) | AI Agent Streaming Architecture Hardening | Implemented |
| [RFC-004](./RFC-004-reducer-based-pipeline-architecture.md) | Reducer-Based Pipeline Architecture | Production Ready ✅ |
| [RFC-005](./RFC-005-event-loop-savecheckpoint-bypass.md) | Event Loop Must Not Bypass SaveCheckpoint Effects | Implemented |
| [RFC-006](./RFC-006-system-test-prompt-permissions.md) | System Test for PROMPT.md Permission Toggle | Implemented |
| [RFC-007](./RFC-007-stale-prompt-replay-postmortem.md) | Post-Mortem: Stale Prompt Replay Across Iterations | Implemented |
| [RFC-008](./RFC-008-external-parallel-worker-orchestration.md) | External Parallel Worker Orchestration for Ralph | Draft |
| [RFC-009](./RFC-009-mcp-agent-orchestrator-communication.md) | MCP-Style Agent-Orchestrator Communication | Draft |
| [RFC-010](./RFC-010-agent-success-ordering-postmortem.md) | Post-Mortem: Agent Success Ordering Regression Across Phases | Implemented |
| [RFC-011](./RFC-011-mcp-tool-availability-postmortem.md) | Post-Mortem: MCP Tool Availability Drift Across Prompt, Harness, and Runtime | Implemented |
| [RFC-012](./RFC-012-parallel-fanout-unreachable-postmortem.md) | Post-Mortem: Parallel Fan-Out Was Never Reachable In Production | Implemented |
| [RFC-013](./RFC-013-fs-churn-fseventsd-reduction.md) | Filesystem Churn Reduction for Long-Running Workflows (fseventsd) | Review |

## Creating a New RFC

1. Copy the template below
2. Name your file `RFC-NNN-short-description.md` where NNN is the next available number
3. Fill in all sections
4. Submit a PR or share for discussion

## RFC Template

```markdown
# RFC-NNN: Title

**RFC Number**: RFC-NNN
**Title**: Full Title Here
**Status**: Draft
**Author**: Your Name
**Created**: YYYY-MM-DD

---

## Abstract

Brief summary of the proposal (2-3 sentences).

---

## Motivation

Why is this change needed? What problem does it solve?

---

## Proposed Changes

Detailed description of the changes.

---

## Implementation Priority

| Item | Effort | Impact | Priority |
|------|--------|--------|----------|
| ... | ... | ... | ... |

---

## Success Criteria

How do we know when this is done?

---

## Risks & Mitigations

What could go wrong and how do we prevent it?

---

## Alternatives Considered

What other approaches were considered and why were they rejected?

---

## References

Links to relevant code, docs, issues.

---

## Open Questions

Unresolved decisions that need input.
```
