# Built with / Inspired by Ralph — Showcase

> **If your project uses Ralph Workflow, the fastest way to support it is to add a credit line
> and a one-line description of what you shipped.** This page is the canonical list — you can be on
> it. The point of this file is to make advocacy a 60-second task, not a long discussion.

Ralph Workflow is a free, open-source, local-first **composable loop framework** for running the
coding agents you already use (Claude Code, Codex, OpenCode) **unattended** — hand it a spec, walk
away, come back to reviewable tested commits. The whole reason it gets a page like this is that
real projects have shipped real software with it. Every entry below has a verifiable artifact.

If you'd rather add your project to the page, see the
[Adding your project](#-adding-your-project-60-seconds) section at the bottom.

---

## ⭐ Confirmed builders — running Ralph in production

### 1. 🌙 Nightcrawler — thebasedcapital

- **What it is:** An autonomous coding orchestrator (the project that kicked off this page).
- **What they built with it:** A multi-step agent loop inspired directly by Ralph's hand-off-a-spec /
  come-back-to-commits model — the same "babysit prompt-by-prompt → hand off the spec" shift that
  Ralph Workflow exists to enable.
- **Why it matters:** Nightcrawler is the project that **first publicly credited Ralph Workflow as
  its inspiration** in its own README. That single credit line has done more for Ralph's
  discoverability than every cold email and blog post in the program combined. It is the canonical
  example of the **organic-word-of-mouth** pattern this project was designed to enable.
- **Maintainer:** https://github.com/thebasedcapital (the maintainer's primary public repo, the
  successor to the original Nightcrawler project that first credited Ralph)

### 2. 🧠 kodezart — YalDan / wringtretsina

- **What it is:** An AI code orchestration service that uses **Ralph Workflow as its underlying
  loop engine** — Claude agents for iterative code generation with quality gates.
- **What they built with it:** A production system that composes Ralph's loop primitives
  (`RalphLoop._execute_node`, `_run_ralph_loop_node`, `_fix_code_node`, `_route_after_review`,
  `_route_after_fix`) into an external product. The maintainer filed
  [PR #33](https://github.com/YalDan/kodezart/pull/33) extending Ralph's
  `_fix_code_node` to reuse the existing `_run_quality_gate` helper — the first PR-level
  architectural contribution from a downstream project.
- **Why it matters:** First Ralph-downstream product to **ship a code-level extension back to the
  parent loop**. They treat Ralph as code they can improve, not just a dependency they consume.
- **Repository:** https://github.com/YalDan/kodezart

### 3. 🛠️ voiceterm — jguida941

- **What it is:** A Rust-based terminal overlay for Claude Code/Codex with voice/STT/macros.
  Ships with a TDD'd Ralph integration that uses Ralph as a **CI quality-gate + fix-command**
  in the mutation-test bridge (`mutation_ralph_workflow_bridge.py`,
  `test_check_coderabbit_ralph_gate.py`, `test_autonomy_workflow_bridge.py`).
- **What they built with it:** A real production CI that wires CodeRabbit + Ralph as **parallel
  quality gates**, with Ralph as the default fix-command when the mutation loop finds a defect.
- **Why it matters:** First evidence of a **CI-integration use case** for Ralph — a positioning
  angle (single-task / CI-quality-gate mode) distinct from the overnight-loop use case. voiceterm
  is also the **highest-purity integrator** in the showcase (12★, active, TDD'd test suite).
- **Repository:** https://github.com/jguida941/voiceterm

### 4. 📚 pr-review-agent-skill — pierodibello (Pietro Di Bello)

- **What it is:** A Claude Code / Codex agent skill that ships a `ralph-wiggum-loop` (the
  ghuntley.com/ralph/ pattern) for unattended PR-comment triage. Each agent invocation does
  exactly one unit of work; the shell loop handles repetition. Stateless agent, stateful files.
- **What they built with it:** A small, focused skill that solves the same problem Ralph solves
  (review a PR unattended without context-exhausting a single session) on the PR-review surface
  instead of the spec-execution surface.
- **Why it matters:** Piero is a **multi-signal warm-pool match** (Codeberg star+watch on
  Ralph-Workflow, ships Ralph-pattern code) and his work is the cleanest demonstration of the
  "small focused agent skill + outer shell loop" pattern that Ralph formalizes.
- **Repository:** https://github.com/xpepper/pr-review-agent-skill

### 5. 🪶 speq-skill — marconae (Marco Nae)

- **What it is:** A spec-driven-development skill for Claude Code/Codex (45★, MIT, Rust). A
  permanent + semantically-searchable spec library (via snowflake-arctic-embed-xs) with
  semantic anchors (TDD/BLUF/ADR) and an "ask me instead of assuming" pattern.
- **What it is NOT:** A competitor. speq-skill is the **SPEC layer** of the stack; Ralph is the
  **EXECUTION layer**. A user can drop a spec into speq-skill, then hand it off to Ralph for the
  overnight loop — the two compose cleanly. The adjacent-tool callout in speq-skill's README
  (OpenSpec/BMAD/SpecKit) is the natural place for Ralph to appear as a complement.
- **Repository:** https://github.com/marconae/speq-skill
- **Blog:** https://deliberate.codes

### 6. 🌙 unattended-loop — endario

- **What it is:** A Claude Code skill for running Claude Code **unattended** on a planned work
  track — overnight, day-trip, multi-hour meeting block. Wraps Claude Code's native `/loop`
  primitive with a 4-file harness and a baked-in engineering playbook (TDD, `/review` on
  meaningful PRs, PR-first workflow, 3-strike failure budget, no-stop policy).
- **What it is NOT:** A competitor. Same ICP (builders who want Claude Code to work overnight),
  same positioning (hand off + walk away), different execution (Claude Code skill + 4-file
  harness vs. Ralph framework).
- **Why it matters:** First peer project in the **"unattended Claude Code"** category with
  similar positioning. Co-mention in awesome-lists would amplify the entire category.
- **Repository:** https://github.com/endario/unattended-loop

### 7. 🤖 ralph-claude-code — frankbria (★9.3K)

- **What it is:** The highest-traffic ralph-family project — a Claude-Code-specific ralph loop
  orchestrator with badges, monorepo awareness, sandbox integration, token cost tracking, and
  Windows/Nix support. 9.3K★, 705 forks, 16 open issues, actively maintained.
- **What they built with it:** A reference implementation that 9.3K★ worth of Claude Code users
  have already adopted. The **"Related projects / See also"** section in this README is the
  highest-leverage surface for cross-project discoverability in the ralph family.
- **Why it matters:** Same archetype as Nightcrawler (peer ralph-family project), at 23× the
  reach. A single `See also` block naming the 3 peer ralph projects (Ralph Workflow +
  speq-skill + endario/unattended-loop) is the single highest-leverage act available for
  star-mover in the program.
- **Repository:** https://github.com/frankbria/ralph-claude-code

---

## 🔍 Hunters — projects we're watching for a credit

These projects don't credit Ralph yet, but their **structure or position** is in the same
category. If you maintain one of them and Ralph fits, the fastest way to support the project is
to add a one-line credit. (Filing an issue that asks "would you consider adding Ralph to your
README's adjacent-tools section?" is also a clean way to start.)

- **Hermes Agent** (~★190K, fastest-growing category competitor) — adjacent positioning,
  high-traffic. Credit would be a category inflection. https://hermes-agent.org
- **Aider** (~★46K) — adjacent positioning. Used in many spec-driven workflows. https://aider.chat
- **Continue** (~★33.6K) — adjacent positioning, dev-tool category. https://continue.dev
- **Conductor OSS** (~★31.9K) — adjacent positioning, parallel orchestrator.
  https://conductor-oss.org

The maintainers of these projects are profiled in
`agents/marketing/curator_outreach_targets.md` (workspace-internal). Outreach is **genuine
discovery questions only** — no backlink ask, no auto-message. Mom-Test voice. Owner-approved,
never templated.

---

## 🏷️ Powered by Ralph — badge

If your project uses Ralph Workflow, add this badge to your README to make the credit visible:

```markdown
[![Powered by Ralph Workflow](https://img.shields.io/badge/powered_by-Ralph_Workflow-2E7D32?style=flat-square&logo=codeberg&logoColor=white)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
```

HTML (if your README renderer prefers it):

```html
<a href="https://codeberg.org/RalphWorkflow/Ralph-Workflow">
  <img src="https://img.shields.io/badge/powered_by-Ralph_Workflow-2E7D32?style=flat-square&logo=codeberg&logoColor=white" alt="Powered by Ralph Workflow" />
</a>
```

The badge links to the canonical Codeberg repo. Stars on the badge page are attributed correctly
to the canonical home (the conversion surface that actually matters for the project).

---

## ✍️ Share-your-run template (60 seconds)

Ran a `ralph` overnight and woke up to reviewable tested commits? Paste this into a GitHub
discussion, a Mastodon post, or a dev.to article:

```markdown
## What I shipped with Ralph Workflow

**Project:** <name + one-line description>
**Spec I gave it:** <one sentence — what was the input?>
**What came back:** <one sentence — what did the agent produce?>
**What I learned:** <one sentence — what would I do differently next time?>
**Repo:** <link to your project>
**Powered by:** [Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
```

The "What I learned" line is the most-valuable part — it's the only thing the rest of us can't
get from the spec. Even a one-line "I learned to cap the test budget at 200/call" is gold.

If you'd rather, **file a GitHub issue on this repo titled `SHOWCASE: <your project>`** and we'll
add the entry to this page within a day. No formatting requirements, no template demand — just
the four lines above and a link.

---

## ➕ Adding your project (60 seconds)

The fastest path:

1. Add a one-line credit to your README — something like
   `Built on [Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow)` is enough.
2. File a GitHub issue on
   [codeberg.org/RalphWorkflow/Ralph-Workflow/issues](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues)
   titled `SHOWCASE: <your project>` with a one-paragraph description of what you shipped and
   a link.
3. We add you to this page within a day, you add the badge above to your README, and the
   discoverability cycle compounds.

If you'd rather not file a public issue, email **ken@ralphworkflow.com** with `SHOWCASE:` in
the subject and the same one-paragraph description. (No marketing follow-up, no signup sequence,
no list — just the showcase addition.)

---

## Why a showcase page matters

A Codeberg star is a **lagging advocacy proxy** for a project whose primary adoption surface is
PyPI. Stars spike on **events**, not on trickle (canonical growth-tactic observation). The
single highest-leverage act in any organic-discovery program is a **peer-builder credit in
another project's README** — one credit line, surfaced in front of the maintainer's existing
audience, with zero amplification cost.

This page exists to make that credit frictionless. If you've shipped something with Ralph, the
60-second template above is the path. If you maintain an adjacent project and Ralph fits, the
**share-your-run template** is the path. Either way, the page is the artifact — and the credit
on it is the conversion surface.

> *"One credited builder > 500 silent installs."* — the Ralph growth canon
