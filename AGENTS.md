# AGENTS.md

## > [!IMPORTANT]
> 
THERE ARE OTHER DEVELOPERS WORKING ON THIS, DO NOT REVERT THEIR CHANGES WORK WITH THEM

## Context — Re-read When Needed
- **Current plan:** `.agent/PLAN.md`
- **Overall objective:** `PROMPT.md`

THERE ARE OTHER DEVELOPERS WORKING ON THIS, DO NOT REVERT THEIR CHANGES WORK WITH THEM
---

## 🚨 TOP PRIORITY: Fix ALL Pre-existing Issues Immediately It does not matter if something is Pre-existing
**Every surfaced issue becomes your #1 priority immediately.** No exceptions, no "pre-existing" carve-outs.

1. Discover any issue → STOP → Fix it completely → Verify clean → Return to original task.

Run `docs/agents/verification.md` before completion. Zero ERROR/WARNING required.

---

## Git: User-Directed Only
**By default, keep git usage read-only. If the current user prompt directly and explicitly requests a git operation, you may perform that specific operation.**

| Allowed by default | Allowed only when directly requested in the user prompt | Still forbidden unless the user explicitly asks for them |
|---------|-----------|-----------|
| `git status`, `git log`, `git diff`, `git show`, `git branch` (list), `git remote -v` | `git add`, `git commit`, `git push`, `git merge`, `git rebase`, `git stash`, `git cherry-pick`, `git revert` | destructive or high-risk git commands such as `git reset --hard`, `git clean`, `git branch -D`, or equivalent force operations |

**MCP git tools follow the same rule.** Only perform the exact git operation the user directly asked for, and do not broaden that permission to other git actions. Hook/marker tampering remains a security violation.

---

## Lint Policy (Strict)
- **`#[allow(...)]` macro — Forbidden.** Zero exceptions. Use `#[expect(..., reason = "...")]` only for external proc-macro output.
- **`.expect()`/`.unwrap()` — Forbidden** except at: `test-helpers/src/lib.rs`, `xtask/src/main.rs`, `ralph-gui/src/main.rs`, boundary modules (`io/`, `runtime/`, `ffi/`, `boundary/`).
- **Functional lints:** Never suppress. Don't fake a boundary module just to silence a lint.
- Check compliance: `cargo xtask lsp-forbidden-allow-expect`
- See `docs/agents/verification.md` for `#[allow]`/`#[expect]` enforcement; `docs/tooling/dylint.md` for boundary module definitions.

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
