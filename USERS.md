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

---

## Plugin & tool integrations — discovered 2026-06-20

### BLEN Inc — [blencorp/lisa](https://github.com/blencorp/lisa)
**"Lisa plans. Ralph does."** — Professional engineering team (BLEN Inc) built an
interactive specification interview workflow for Claude Code that conducts in-depth
feature interviews and generates comprehensive specs designed to feed directly into
Ralph Workflow. Exports PRDs in Ralph-compatible format (JSON with `branchName`,
`-progress.txt` marker file). Cross-provider CLI (Claude Code, OpenCode, Cursor,
Codex, Copilot). Public npm package `@blen/lisa`.
*(discovered 2026-06-20 via gh search repos — explicit Ralph integration + Ralph-format output)*

### Alexey Krol — [greatakela/framework-router](https://github.com/greatakela/framework-router)
Claude Code plugin that installs and routes a combined GSD + gstack + Superpowers
workflow into any project — **plus optional Ralph autonomous-build loop.** Ships
`/enable-ralph A|B` command that switches on autonomous build once spec + roadmap
are locked. Ralph included as payload (no install needed).
*(discovered 2026-06-20 via gh search repos — `/enable-ralph` command, Ralph payload)*

### gmickel — [gmickel/flow-next](https://github.com/gmickel/flow-next)
Spec-driven AI workflow plugin (Claude Code, OpenAI Codex, Factory Droid) with
zero-dep task tracking, worker subagents, **Ralph autonomous mode**, and cross-model
reviews. Includes spoken command `/flow-next:ralph-init` and a Ralph TUI dashboard.
*(discovered 2026-06-20 via gh search repos — Ralph autonomous mode, TUI, `/flow-next:ralph-init`)*

### Cemini (multi-wiki research network) — [cemini23/cemini-claude-code-CCC](https://github.com/cemini23/cemini-claude-code-CCC)
Public meta-wiki (part of Cemini's 8-wiki federation) documenting agent orchestration
in Cursor and Claude Code — explicitly names Ralph as one of the canonical patterns
alongside `/goal` and OpenSpec. Publicly accessible methodology documentation with
YouTube channel (@Cemini23) and newsletter (Outlier Weekly).
*(discovered 2026-06-20 via gh search repos — /goal·Ralph·OpenSpec patterns)*

### John Ezra — [john-ezra/open-ralph](https://github.com/john-ezra/open-ralph)
**"Light opencode plugin for the Ralph workflow."** — 5th independent plugin
integration. npm package `@john-ezra/open-ralph`. OpenCode-native, 3-phase workflow
(define → plan → build), Docker mode support. Published to npm for direct install.
*(discovered 2026-06-20 via gh search repos — npm package, 1★, OpenCode integration)*

### Jason Salomon-Rinnert — [aslomon/effectum](https://github.com/aslomon/effectum)
**"Describe what you want. Get production-ready code."** — The most comprehensive
Ralph-adjacent product discovered to date. Full autonomous development framework FOR
Claude Code with PRD Workshop, 10 workflow commands, 8 quality gates, and overnight
Ralph Loop builds. Permanent alias `/ralph-loop` → `effect:dev:run` in production.
446 tests, 32 primary commands, 25 agent specializations, 43+ skills. npm package
`@aslomon/effectum`. Explicitly credits Ralph Workflow as its core loop engine.
This is the deepest integration signal found — a production framework with Ralph
as a named permanent command alias.
*(discovered 2026-06-20 via gh search repos — 2★, `/ralph-loop` alias, 446 tests)*

### Gens AI — [Gens-ai/autopilot](https://github.com/Gens-ai/autopilot)
**"Autonomous TDD workflow for Claude Code using Ralph Loop."** — Production product
with 14★ and 4 forks. Built-in loop mechanism credited to the Ralph Wiggum technique.
No external plugins required — the Ralph Loop pattern is baked into the core workflow.
*(discovered 2026-06-20 via gh search repos — 14★, 4 forks, built-in Ralph loop)*

## More builders — discovered 2026-06-20

### AutoMedica Labs — [auto-medica-labs/ralph-template](https://github.com/auto-medica-labs/ralph-template)
**"An automatic Ralph workflow for autonomous software development tasks using the
OpenCode CLI tool."** — First healthcare/health-tech domain adoption discovered.
5★, actively maintained (last updated May 2026). Uses OpenCode as the agent runtime
rather than Claude Code — confirms Ralph's vendor-neutral value proposition.
*(discovered 2026-06-20 via gh search repos — 5★, OpenCode, health-tech domain)*

### Ariceb — [ariccb/helixir-ralph-workflow](https://github.com/ariccb/helixir-ralph-workflow)
**"Coordinated ralph workflow integrating flow-next task management with gstack
quality gates."** — Demonstrates Ralph as a composition layer in a multi-tool
orchestration stack: flow-next (task management) + gstack (quality gates) + Ralph
(autonomous execution loop). Evidence that Ralph is being used as a protocol
component in larger agent orchestration architectures.
*(discovered 2026-06-20 via gh search repos — multi-tool orchestration pattern)*

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
