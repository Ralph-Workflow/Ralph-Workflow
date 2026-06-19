# Who's Using Ralph Workflow?

> These people and projects are using Ralph Workflow — discovered through code-level search,
> issue trackers, and public references. Ralph Workflow is an execution harness (you clone or
> template it), not a library (you `pip install` it), so the standard "who depends on us"
> metric is systematically blind. A team can run Ralph Workflow nightly on real production
> code without ever declaring it as a dependency — because Ralph is the runner, not the
> library.
>
> This page exists because adoption is invisible to `pip install` counts. Every entry below
> was verified through source artifacts, issues, or public discussion. If your project should
> be here, open a PR or issue.

---

## Production & active development users

### David Gowrie @ Grafana Labs — [dgowrie/claude-workflows](https://github.com/dgowrie/claude-workflows/issues/52)
Runs Ralph Workflow against a real Grafana PRD with 10 sub-issues. Filed a rigorous 6-gap
environment HALT analysis documenting production constraints. Uses `ralph.sh --autonomous`.
*(discovered 2026-06-17 via issue #52)*

### YalDan — [YalDan/kodezart](https://github.com/YalDan/kodezart)
Built kodezart, a production AI code orchestration service that consumes Ralph's loop engine
at engineering depth: 1,037-line ralph_workflow.py import, 1,486-line ralph_loop.py. Filed 3
convergence bugs (#32 `_fix_code_node` single-shot, #25 AC-design pattern thrash, #19
unsatisfiable criteria). Deepest technical integration discovered so far.
*(discovered 2026-06-17 via source-level dependency)*

### Mat Warger — [mwarger/ralph-crispies](https://github.com/mwarger/ralph-crispies)
Building the 4th-generation Ralph descendant (Forge → Super-Ralph → Crispies lineage). Now
adding Promptfoo Workflow Evals (testing/evaluation infrastructure) + Flue backend integration
on top of the Ralph loop pattern. Two active PRDs labeled ready-for-agent.
*(discovered 2026-06-17 via issues #1, #10)*

### qte77 — [ralph-loop-cc-tdd-wt-vibe-kanban-template](https://github.com/qte77/ralph-loop-cc-tdd-wt-vibe-kanban-template)
Language-agnostic Ralph Loop template with plugin scaffold architecture. Designing
self-evolving offspring repos (ralph-engineering, ralph-research). 107 repos, 10 followers.
*(discovered 2026-06-18 via issue #32)*

### heinschulie — [heinschulie/babylon](https://github.com/heinschulie/babylon)
Ralph v6 pipeline validation with a 5-step pipeline: consult → TDD → refactor → review →
verify. Active discussion thread with 12 comments documenting real Ralph pipeline usage.
*(discovered 2026-06-17 via issue #158)*

### oneplusn-dev — Chinese AI developer community article
Published an article on Loop Engineering (2026-06-17) explicitly crediting Ralph Workflow as
the original implementer of the loop engineering pattern. First known non-English-language
ecosystem reference.
*(discovered 2026-06-17 via public article)*

---

## More builders — discovered 2026-06-19

### rickvian — [rickvian/ralph-workflow](https://github.com/rickvian/ralph-workflow)
Active fork/branch with 4 open feature issues (devcontainer templates, setup wizard,
version tracking scaffolds, and a `ralph-version` file proposal). Building on Ralph
Workflow as a base with new developer-experience features.
*(discovered 2026-06-19 via gh search code — 4 open enhancement issues)*

### Algorand Developer Relations — [algorand-devrel/algorand-agent-skills](https://github.com/algorand-devrel/algorand-agent-skills)
Official Algorand blockchain developer relations team maintains a Ralph Workflow
plan template (`ralph/plan.md`) as part of their agent skills toolkit. First known
blockchain-ecosystem adoption.
*(discovered 2026-06-19 via gh search code)*

### bastani-inc — [bastani-inc/atomic](https://github.com/bastani-inc/atomic)
Atomic CLI (enterprise AI coding tool) references Ralph Workflow extensively in their
RFC for workflow SDK inline mode — cites Ralph's context clearing, task list lifecycle,
and orchestration state as design inputs. Enterprise-level pattern adoption.
*(discovered 2026-06-19 via gh search code — RFC document with multiple Ralph references)*

## Pattern adopters — projects implementing the Ralph loop pattern

These projects independently converged on the same pattern (markdown-plan + progress.json +
sentinel-based termination) — confirming the loop engineering pattern is a real architectural
discovery, not a single-project coincidence.

- **[Martingale42 / obra/superpowers#1725](https://github.com/obra/superpowers/issues/1725)** — Resumable multi-agent execution pipeline with `progress.json` + wake-up file. Verbatim converged on the Ralph pattern. *(2026-06-10)*
- **[marconae/speq-skill](https://github.com/marconae/speq-skill)** — Spec-driven skill for Claude Code. Adjacent/complementary lane. *(2026-06-09)*
- **[xpepper/pr-review-agent-skill](https://github.com/xpepper/pr-review-agent-skill)** — Ralph-wiggum-loop — same pattern. *(2026-06-10)*
- **[endario/unattended-loop](https://github.com/endario/unattended-loop)** — Parallel implementation of the unattended loop pattern. *(2026-06-10)*

---

## Are you using Ralph Workflow?

If you're running Ralph Workflow, building on it, or have implemented the loop engineering
pattern — we'd love to hear about it. File an issue on the repo or open a PR to add your
project here. It takes 60 seconds and helps everyone understand what this tool can do.

→ [Codeberg: RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow)  
→ [GitHub mirror: Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow)
