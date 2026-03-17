# AGENTS.md

## Context — Re-read When Needed
- **Current plan:** `.agent/PLAN.md`
- **Overall objective:** `PROMPT.md`

---

## 🚨 TOP PRIORITY: Fix ALL Pre-existing Issues Immediately It does not matter if something is Pre-existing
**Every surfaced issue becomes your #1 priority immediately.** No exceptions, no "pre-existing" carve-outs.

1. Discover any issue → STOP → Fix it completely → Verify clean → Return to original task.

Run `docs/agents/verification.md` before completion. Zero ERROR/WARNING required.

---

## Git: Read-Only Only
**Ralph is the ONLY entity allowed to commit.**

| Allowed | Forbidden |
|---------|-----------|
| `git status` | `git commit`, `git push`, `git add`, `git merge`, `git rebase`, `git reset`, `git stash` (except list), `git branch -D`, `git clean`, `git cherry-pick`, `git revert` |
| `git log`, `git diff`, `git show` | |
| `git branch` (list), `git remote -v` | |

**MCP git tools equally forbidden.** Hook/marker tampering = security violation. Bypass is futile—protections reinstall every run.

---

## Lint Policy (Strict)
- **`#[allow(...)]` — Never.** Zero exceptions. Use `#[expect(..., reason = "...")]` only for external proc-macro output.
- **`.expect()`/`.unwrap()` — Forbidden** except at: `test-helpers/src/lib.rs`, `xtask/src/main.rs`, `ralph-gui/src/main.rs`, boundary modules (`io/`, `runtime/`).
- **Functional lints:** Never suppress. Don't fake a boundary module just to silence a lint.

---

## Required Workflows
| Trigger | Action |
|---------|--------|
| Feature/bugfix | Use `test-driven-development` skill first |
| Debugging | Use `systematic-debugging` skill first |
| Angular/GUI | Use Angular MCP + `frontend-angular` skill |
| Styling/visual | Use `frontend-design` skill |
| Any pipeline/reducer change | Read architecture docs first |
| Any test work | Read `docs/agents/testing-guide.md` |
| Filesystem I/O | Read `docs/agents/workspace-trait.md` |

---

## Non-Negotiables
- TDD required (failing test first)
- Verification required before PR
- GUI: Angular v21 + Tailwind (prefer)
- No tech debt (prefer refactor)
- No dead code (`#[allow(dead_code)]` forbidden)
- Never weaken lint rules

---

## Key References
| Topic | File |
|-------|------|
| Verification commands | `docs/agents/verification.md` |
| Testing guide | `docs/agents/testing-guide.md` |
| Architecture | `docs/code-style/architecture.md`, `docs/architecture/event-loop-and-reducers.md` |
| Dylint lints | `docs/tooling/dylint.md` |

---

## Other Rules
- **YOLO mode:** Required for automated file ops
- **Temp files:** Use `tmp/` at repo root
- **External deps:** Research via context7, then official docs
- **File creation:** No temp `.md` in root/docs; update stale docs when touched
