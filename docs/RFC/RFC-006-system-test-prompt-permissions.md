# RFC-006: System Test for PROMPT.md Permission Toggle

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


**RFC Number**: RFC-006
**Title**: System Test for PROMPT.md Permission Toggle
**Status**: Implemented
**Author**: Codex
**Created**: 2026-02-10

---

## Abstract

Add a system test that validates real filesystem read-only and writable
behavior for `PROMPT.md` via `WorkspaceFs`. This test ensures the boundary
implementation behaves correctly across platforms where `MemoryWorkspace`
cannot emulate permissions.

---

## Motivation

Prompt permission toggling relies on the real filesystem implementation
(`WorkspaceFs`). `MemoryWorkspace` is a no-op for permissions by design, so
unit and integration tests cannot verify actual permission changes. A system
test is required to validate the boundary behavior.

---

## Proposed Changes

- Add a system test module under `tests/system_tests/` that:
  - Creates a real temporary repository with `PROMPT.md`
  - Uses `WorkspaceFs::set_readonly` and `WorkspaceFs::set_writable`
  - Asserts filesystem-level read-only and writable permissions
- Register the module in `tests/system_tests/main.rs`

---

## Implementation Priority

| Item | Effort | Impact | Priority |
|------|--------|--------|----------|
| System test for PROMPT.md permissions | Small | High | P0 |

---

## Success Criteria

- System test passes on supported platforms
- `PROMPT.md` is read-only after `set_readonly`
- `PROMPT.md` is writable after `set_writable`

---

## Risks & Mitigations

- **Risk**: Platform differences in permission semantics.
  - **Mitigation**: Use `#[cfg(unix)]` and `#[cfg(windows)]` checks.

---

## Alternatives Considered

- **Integration test with `MemoryWorkspace`**: Rejected because it cannot
  validate real permission toggling.

---

## References

- `tests/system_tests/SYSTEM_TESTS.md`
- `ralph-workflow/src/workspace/workspace_fs.rs`

---

## Open Questions

None.
