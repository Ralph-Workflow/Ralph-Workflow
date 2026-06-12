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

> **Status as of 2026-06-12: this list is empty.** No public project on the open web currently
> credits Ralph Workflow in a way that we have *verified* with the evidence gate below. This is
> the honest state of the project. It is the correct state, and it is the only state we will
> publish.
>
> The previous version of this section listed 7 "Confirmed builders" (Nightcrawler, kodezart,
> voiceterm, pr-review-agent-skill, speq-skill, unattended-loop, ralph-claude-code). All 7 were
> added based on naming-association ("the project uses the word Ralph") rather than
> artifact-verification ("the project's `pyproject.toml` / `requirements.txt` / source code
> imports `ralph-workflow`, or its README contains a credit line"). Per-entry verification
> (cloned each repo, grepped for `ralph-workflow` / `RalphWorkflow` / `from ralph` / PyPI dep /
> README credit) returned **0 of 7** as actually using Ralph Workflow. That failure is the
> reason this page is being rewritten — and the reason the evidence gate below is mandatory.
>
> If you maintain a project that *actually uses* Ralph Workflow, the path is the
> [Adding your project](#-adding-your-project-60-seconds) section at the bottom. The first
> verified entry goes here.

### Evidence gate (binding on every entry that goes above this line)

Every "Built with Ralph Workflow" / "Confirmed builder" / "Powered by Ralph" claim MUST include
a `verify: <evidence>` line in the same row. Acceptable evidence:

- **(a) PyPI / lockfile pin:** a literal `ralph-workflow==X.Y.Z` line from the project's
  `pyproject.toml`, `requirements*.txt`, `poetry.lock`, `uv.lock`, or `Pipfile`, with the file
  path. Not "depends on Ralph" — the actual line.
- **(b) Source import:** a quoted `import ralph` / `from ralph ...` statement with file path
  and line range, from the project's actual repo at a specific commit SHA.
- **(c) README credit:** a quoted credit line + URL to the README of the project, at a
  specific commit SHA.

No other form of evidence is acceptable. Specifically, the following do NOT count as
verification, ever:

- GitHub stars, traffic, or repo size.
- "Same ICP," "composes cleanly," "the maintainer follows us," or "they're in our space."
- Naming association (the word "Ralph" or "loop" or "agent" in a project name or README).
- Maintainer self-report without an attached artifact (no PR, no commit, no quoted line).
- Maintainer intent ("they told us on a call they'd add a credit") — only the published
  artifact counts.

If the `verify:` line is missing, weak, or stale, the claim is **unverified and MUST NOT be
published.** Default action: leave the project off the page until evidence exists.

### Auto-rejection patterns (enforced by `scripts/verify_social_proof.py`)

The following patterns in any public-facing markdown file (`README.md`, `*.md` under
`content/blog/`, `docs/`, landing pages, comparison pages) will cause the social-proof
verifier to fail the build. Each is a pattern that has been used to publish a false claim
in the past, and each is now banned at the source.

- `"Nightcrawler credits Ralph Workflow as its inspiration"` — FALSE; Nightcrawler credits
  `ghuntley.com/ralph` (Geoffrey Huntley's Ralph-loop pattern), not Ralph Workflow.
- `"~1,300 installs/month on PyPI"` and any close variant — STALE/WRONG; pepy.tech
  2026-06-12 reports 4,047 in the last 30 days. Use the sourced, date-stamped line in
  `RALPH_WORKFLOW_POSITIONING.md`; re-verify before any change.
- Any "X credits Ralph Workflow" / "X built with Ralph" claim in the README hero or
  social-proof line that does not have a corresponding `verify: <evidence>` line in the file.
- Any installer / download / star count not paired with a `(source, date)` annotation.

If a future legitimate claim is blocked by a false-positive pattern, update the script — do
not delete the script and do not weaken the gate. The gate is the only thing standing between
this project and a repeat of the 2026-06-11 SHOWCASE.md failure.

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
