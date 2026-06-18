# Ralph Workflow — Ecosystem Map

> **Last updated:** 2026-06-18 · This page collects known projects using, building on, or
> referencing Ralph Workflow, discovered through code-level search (GitHub search, SourceGraph,
> dependency analysis). It is distinct from the
> [Showcase](SHOWCASE.md) — which lists **confirmed builders** with verifiable artifact evidence
> (pyproject.toml dependency, source import, or README credit line).
>
> The entries below do NOT yet meet the Showcase evidence gate. They represent the part of the
> adoption curve that is invisible to `pip install` metrics: projects that use Ralph Workflow as
> a pattern, a loop engine, a workflow template, or a discussion reference — without declaring
> it as a formal dependency.

---

## Observed usage — projects building with or referencing Ralph

| Project | Maintainer | What they're doing | Discovery date | Evidence |
|---------|-----------|-------------------|----------------|----------|
| [dgowrie/claude-workflows](https://github.com/dgowrie/claude-workflows/issues/52) | David Gowrie @ Grafana Labs | Filed 6-gap analysis running Ralph against real Grafana PRD (10 sub-issues), ran `ralph.sh --autonomous` | 2026-06-17 | Issue #52 documents production use, environment HALT report |
| [YalDan/kodezart](https://github.com/YalDan/kodezart/issues/25) | YalDan | Production code orchestration service consuming Ralph's loop engine (ralph_workflow.py 1037L, ralph_loop.py 1486L) | 2026-06-17 | Source-level dependency; 3 convergence bugs filed (#32, #25, #19) |
| [mwarger/ralph-crispies](https://github.com/mwarger/ralph-crispies) | Mat Warger | 4th-generation Ralph descendant: Forge → Super-Ralph → Crispies lineage | 2026-06-17 | Issue #1 documents QRDS-PI cycle built on Ralph beads |
| [qte77/ralph-loop-cc-tdd-wt-vibe-kanban-template](https://github.com/qte77/ralph-loop-cc-tdd-wt-vibe-kanban-template) | qte77 | Language-agnostic Ralph Loop template with plugin scaffold architecture, designing self-evolving offspring repos (ralph-engineering, ralph-research) | 2026-06-18 | Issue #32 is a design document for offspring repos; 107 repos, 10 followers |
| [heinschulie/babylon](https://github.com/heinschulie/babylon) | heinschulie | Ralph v6 pipeline validation, 5-step pipeline (consult→tdd→refactor→review→verify) | 2026-06-17 | Issue #158 has 12 comments documenting Ralph pipeline usage |
| [oneplusn-dev](https://github.com/oneplusn-dev) | oneplusn-dev | Chinese AI dev community article on Loop Engineering (2026-06-17) explicitly credits Ralph Workflow as the original implementer | 2026-06-17 | Public article with explicit Ralph Workflow credit |

## Pattern references — projects implementing the Ralph loop pattern

| Project | What they ship | Discovery date |
|---------|---------------|----------------|
| [obra/superpowers#1725](https://github.com/obra/superpowers/issues/1725) | Martingale42's resumable multi-agent execution pipeline — verbatim converged on `progress.json` + wake-up file (2026-06-10) | 2026-06-10 |
| [marconae/speq-skill#14](https://github.com/marconae/speq-skill) | Spec-driven skill for Claude Code — author confirmed adjacent/complementary lane | 2026-06-09 |
| [xpepper/pr-review-agent-skill#2](https://github.com/xpepper/pr-review-agent-skill) | Ralph-wiggum-loop — ships the same pattern (markdown-plan + sentinel-based termination) | 2026-06-10 |
| [endario/unattended-loop](https://github.com/endario/unattended-loop) | Unattended loop pattern — parallel implementation | 2026-06-10 |

---

## Why this page exists

The standard adoption metric for an open-source tool is "who depends on it" — pyproject.toml
entries, npm installs, source imports. For an **infrastructure tool** like Ralph Workflow (an
execution harness you clone or template, not a library you `pip install`), that metric is
systematically blind. A project can run Ralph Workflow nightly on real production code without
ever declaring it as a dependency — because Ralph is the runner, not the library.

This page captures what `pip install` misses. It is maintained through **code-level search**
(GitHub search for `ralph-workflow`, `ralph.sh`, `ralph_workflow.py`, `progress.json` pattern
matches, SourceGraph references) and will be updated as new projects surface.

If your project appears here and should be on the [Showcase](SHOWCASE.md) instead, add the
verification evidence (pyproject.toml pin, source import line, or README credit) — the path
is a 60-second task described in [SHOWCASE.md](#-adding-your-project-60-seconds).

---

*Found a project using Ralph? Search tools: `gh search code 'ralph-workflow' --limit 50`,
`gh search code 'ralph_workflow.py' --limit 30`, `gh search issues 'Ralph Workflow' --state=open --limit 20`.*
