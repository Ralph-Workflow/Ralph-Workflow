# Claims Ledger — Ralph Workflow Public Surfaces

> Every factual claim on every public-facing surface. Verified = ✅, False = ❌,
> Unverified = ⬜, Stale = ⏳, Conflated = 🔀, Removed = 🗑️.
>
> **Rule:** No claim may appear on a public surface without a corresponding entry here
> marked ✅ or with a `(source, date)` citation inline.

**Last audit:** 2026-07-07

---

## Audit Summary (2026-07-07)

| Category | Count |
|---|---|
| Total claims cataloged | 11 |
| Verified true | 11 |
| False and fixed (historical) | 12 |
| Conflated (pattern≠product) | 7 |
| Removed / no longer on public surface | 18 |
| Unverified (open) | 0 |

The 2026-07-07 refresh consolidated the public surfaces. The 2026-06-30 ledger
tracked four near-duplicate community files (`USERS.md`, `SHOWCASE.md`,
`ECOSYSTEM.md`, `COMPARISONS.md`); all four are now retired in favor of a
single `USERS.md` as the canonical community surface. SHOWCASE.md, ECOSYSTEM.md,
COMPARISONS.md, and CREDIT_TEMPLATE.md are deleted. The corresponding claim
rows (E1-E5, C1-C3, SC1-SC3) are preserved in the Historical Surfaces
section below as a fabrication-guard audit trail. The 28 SEO sphinx pages
deleted in the same pass are also captured as a single 🗑️ row.

The current surviving public surfaces are: `README.md`, `START_HERE.md`,
`USERS.md` (canonical community directory), `ralph-workflow/docs/sphinx/`
(operator manual), and the repo-root `docs/` map and contributor pages.

---

## Fabrication register (confirmed hallucinations — historical record)

These claims were identified and removed during prior audits. They are listed
here as a historical guard, not as live public claims.

1. **Continue "Acquired by Cursor · Jun 2026"** — zero evidence. FIXED 2026-06-16.
2. **Nightshift "by Orbit/Recusive" + "935 tests, 80 merged PRs, 28 modules"** — org doesn't exist on GitHub, repo is a 0-star test project. FIXED 2026-06-16.
3. **kodo "ottiwroteit"** — wrong owner. Actual: ikamensh/kodo. FIXED 2026-06-16.
4. **aicom specific pipeline numbers (61K LOC, 12 agents, 5 quality gates, 11-state, 34 transitions, 6+ providers)** — none appear in README or verifiable source. FIXED 2026-06-16.
5. **Symphony "14 merged PRs from 20 issues in a four-day sprint"** — sourced from unverifiable "early-adopter reports." FIXED 2026-06-16.
6. **SHOWCASE.md entries #1-#3** — all conflated pattern convergence with product credit. FIXED 2026-06-16. The SHOWCASE.md surface itself was deleted 2026-07-07; the entries are preserved in the Conflation Register for audit-trail purposes.
7. **"OpenAI Symphony is an open-source Elixir service"** — actually an internal OpenAI tool, community reimplementations exist. FIXED 2026-06-16.
8. **"miserness/ralphy" (goldrush + why-5 posts)** — GitHub user `miserness` exists but has NO repo called `ralphy`. Project fabricated in two blog posts. FIXED 2026-06-16.
9. **"cv/ralph-addons"** — wrong owner, actual is `cvemprala/ralph-addons`. FIXED 2026-06-16.
10. **"Ralph-code (May 2026)"** — created Feb 25 not May. Timeline fabricated. FIXED 2026-06-16.
11. **"5 independent reinventions"** — only 4 exist (ralphy fabricated), framing as "Ralph Workflow ecosystem" is conflation (all credit Ralph Loop pattern, not RW product). FIXED 2026-06-16.
12. **Bernstein GitHub URL "Anthraxa1/Bernstein" + agent counts** — URL 404s on GitHub. Real repo: `sipyourdrink-ltd/bernstein` (579⭐, Apache 2.0). "46 agents" → actually 44 (per PyPI: "44 CLI agent adapters in v2.2.x"). "12 agents racing end-to-end" in CTA → unsourced, removed. FIXED 2026-06-16 22:20.

---

## Verified true register

1. Emdash "206 HN points" → HN id 47140322 ✅ (historical; not on current public surface)
2. Emdash ~4,877 GitHub stars → generalaction/emdash ✅ (historical; not on current public surface)
3. Freestyle "322 HN points" → HN id 47663147 ✅ (historical; not on current public surface)
4. kodo exists → ikamensh/kodo, 107⭐, MIT ✅ (historical; not on current public surface)
5. aicom exists → alexar76/aicom, 1⭐, MIT ✅ (historical; not on current public surface)
6. 12,304 lifetime / 4,430 30d PyPI downloads → pepy.tech ✅ (historical; not on current public surface)
7. Bernstein 320,000+ total installs → pepy.tech ✅ (historical; not on current public surface)
8. umputun/ralphex exists → 1,274⭐, created Jan 2026 ✅
9. cvemprala/ralph-addons exists → 2⭐, created Jan 2026 ✅ (historical; not on current public surface)
10. daegwang/ralph-code exists → 4⭐, created Feb 2026 ✅ (historical; not on current public surface)
11. happycastle114/oh-my-openclaw exists → 181⭐, created Feb 2026 ✅ (historical; not on current public surface)
12. GitHub user "miserness" exists but has NO ralphy repo → verified via API ✅

---

## Current public surfaces

### README.md (`/README.md`)

| # | Claim | Status | Evidence |
|---|---|---|---|
| R1 | Ralph Workflow is the autopilot for coding agents / AI agent orchestrator | ✅ | Product positioning statement |
| R2 | Free and open-source | ✅ | License section: AGPL-3.0-or-later |
| R3 | Python ≥ 3.12 runtime | ✅ | README runtime section |
| R4 | Supported agents: Claude Code, Codex, OpenCode, Nanocoder, AGY, Pi | ✅ | README supported agents section + agent-compatibility.md |
| R5 | Codeberg primary repo + GitHub mirror | ✅ | `(verify: repo-exists)` annotations |
| R6 | PyPI package `ralph-workflow` | ✅ | <https://pypi.org/project/ralph-workflow/> |
| R7 | `ralph --init`, `ralph --diagnose`, `ralph` commands | ✅ | README install/run block |
| R8 | Ralph Workflow ecosystem → USERS.md | ✅ | `Ecosystem and attribution` section points at USERS.md |

### START_HERE.md (`/START_HERE.md`)

| # | Claim | Status | Evidence |
|---|---|---|---|
| S1 | Same autopilot positioning as README | ✅ | Mirrors README |
| S2 | `pipx install ralph-workflow` install path | ✅ | Documented inline |
| S3 | First-run command sequence | ✅ | Documented inline |

### USERS.md (`/USERS.md`) — canonical community directory

| # | Claim | Status | Evidence |
|---|---|---|---|
| U1 | Listed Featured projects (5) exist and implement Ralph Loop | ✅ | Each row has `(verify: repo-exists)` |
| U2 | "Verified Ralph Workflow Credits: 0" | ✅ | Honesty marker per the project's social-proof convention |
| U3 | Listed Pattern-implementation repos (32) exist | ✅ | Each row has `(verify: repo-exists)` |
| U4 | Listed Shadow-ecosystem entries (15) exist | ✅ | Each row has `(verify: repo-exists)` |
| U5 | Listed Active-adopters entries (13) exist | ✅ | Each row has `(verify: repo-exists)` |
| U6 | awesome-ralph community hub exists | ✅ | `(verify: repo-exists)` |
| U7 | Ralph Loop pattern attributed to Geoffrey Huntley | ✅ | Links to ghuntley.com/ralph |
| U8 | "How to add your project" badge markdown renders | ✅ | The `built-with-ralph-loop` badge in the project repo |

### ralph-workflow/docs/sphinx/index.rst + manual home

| # | Claim | Status | Evidence |
|---|---|---|---|
| M1 | Canonical autopilot positioning matches README | ✅ | `index.rst` lead paragraph |
| M2 | First-run 6-step block matches README | ✅ | `index.rst` install/run block |
| M3 | Supported agent list matches README | ✅ | `agent-compatibility.md` matrix |

### ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md (canonical watchdog/timeout page; `docs/agents/timeout-policy.md` is now a one-paragraph redirect stub)

| # | Claim | Status | Evidence |
|---|---|---|---|
| T1 | Five `WorkspaceChangeKind` values: source, log, cache, artifact, other | ✅ | Doc table on the canonical page + regression tests |
| T2 | `agent_workspace_change_weights` config key | ✅ | Doc on the canonical page + tests pinned to canonical home |

---

## Historical surfaces (deleted 2026-07-07)

### Surface: SHOWCASE.md (`/SHOWCASE.md`)

🗑️ Deleted 2026-07-07. The 5 featured entries were merged into USERS.md
"Featured projects" section. The historical claim rows below are preserved
for the fabrication-guard audit trail.

| # | Claim | Status | Evidence |
|---|---|---|---|
| SC1 | Featured projects exist and implement Ralph Loop | ✅ | Each row has `(verify: repo-exists)` |
| SC2 | "Verified Ralph Workflow Credits: 0" | ✅ | Honesty marker |
| SC3 | 46+ projects across USERS.md and ECOSYSTEM.md | ✅ | Live repo links; count refreshed 2026-06-30 |

### Surface: ECOSYSTEM.md (`/ECOSYSTEM.md`)

🗑️ Deleted 2026-07-07. The 3 unique Active-community-projects entries
(Th0rgal, AnandChowdhary, vercel-labs) were merged into USERS.md
Pattern-implementations table. The historical claim rows are preserved
for the fabrication-guard audit trail.

| # | Claim | Status | Evidence |
|---|---|---|---|
| E1 | 46+ projects across USERS.md and ECOSYSTEM.md | ✅ | Live repo links; count refreshed 2026-06-30 |
| E2 | Listed active community projects exist | ✅ | Each row has `(verify: repo-exists)` |
| E3 | Listed "in the wild" integrations exist | ✅ | Each row has `(verify: repo-exists)` |
| E4 | awesome-ralph community hub exists | ✅ | `(verify: repo-exists)` |
| E5 | Ralph Loop pattern attributed to Geoffrey Huntley | ✅ | Links to ghuntley.com/ralph |

### Surface: COMPARISONS.md (`/COMPARISONS.md`)

🗑️ Deleted 2026-07-07. The per-tool comparison content was already covered
by `agent-compatibility.md` (per-agent matrix with caveats). The historical
claim rows are preserved for the fabrication-guard audit trail.

| # | Claim | Status | Evidence |
|---|---|---|---|
| C1 | Compared tools' repos exist | ✅ | Each row has `(verify: repo-exists)` |
| C2 | 46+ projects in ecosystem | ✅ | Cross-reference to USERS.md / ECOSYSTEM.md |
| C3 | Star counts rendered live from source repo pages | ✅ | Documented inline; audited 2026-06-30 |

### Surface: 28 SEO sphinx pages

🗑️ Deleted 2026-07-07. The 28 sphinx pages
(`ai-agent-orchestration-cli.md`, `ai-agent-workflow-composer.md`,
`ai-coding-workflow-automation.md`, `after-your-first-run.md`,
`bounded-autonomy-for-unattended-coding.md`, `claude-code-approval-mode.md`,
`claude-code-automation.md`, `claude-code-codex-workflow.md`,
`claude-code-run-until-done.md`, `free-open-source-proof.md`,
`good-unattended-ai-coding-task.md`, `how-to-tell-if-an-ai-coding-task-is-actually-done.md`,
`open-source-ai-coding-orchestrator.md`, `overnight-demo-real.md`,
`ralph-workflow-vs-aider.md`, `ralph-workflow-vs-claude-code.md`,
`ralph-workflow-vs-codex-cli.md`, `ralph-workflow-vs-google-anti-gravity.md`,
`ralph-workflow-vs-opencode.md`, `remote-supervision-of-coding-agents.md`,
`reviewable-output.md`, `run-claude-code-overnight-without-babysitting.md`,
`spec-driven-ai-agent.md`, `unattended-coding-agent.md`,
`what-a-good-ai-coding-finish-receipt-looks-like.md`,
`what-breaks-first-with-multiple-coding-agents.md`,
`which-agent-should-i-start-with.md`,
`why-worktrees-are-not-enough.md`) were the autopilot+Codeberg-CTA SEO
keyword-stuffed pages. Their claim rows were (a) duplicated the canonical
positioning language verbatim, (b) restated the agent matrix already on
`agent-compatibility.md`, and (c) carried embedded review notes. They
have been replaced by the surviving canonical surfaces.

### Surface: Compare Page (`ralphworkflow.com/compare`)

🗑️ The local `COMPARISONS.md` was rewritten on 2026-06-30 and deleted 2026-07-07.
The legacy compare-page claims about Continue, Nightcrawler, Emdash, Twill,
Symphony, kodo, Nightshift, Bernstein, aicom, Freestyle, and Claude Code
Routines are no longer asserted in this repository. They are retained in
the Fabrication Register and Verified True Register above for historical
context only.

### Surface: Blog Posts

🗑️ The blog-post claim rows (B1-B13) tracked claims in external publication
posts. Those posts are not part of the maintained repository public surfaces and
the specific claims are no longer asserted here. Past fixes are recorded in the
Fabrication Register and Conflation Register.

---

## Conflation Register (Ralph Loop ≠ Ralph Workflow)

Claims that treated pattern credit as product credit. The original entries
were fixed on 2026-06-16 in the now-deleted SHOWCASE.md and COMPARISONS.md
surfaces; the entries below are preserved as a fabrication-guard audit
trail. The 2026-07-07 docs cleanup deleted both surfaces; the table below
is the historical record.

| # | Where | The Claim | Reality |
|---|---|---|---|
| X1 | SHOWCASE.md Entry #1 | "Peer-engineer credit of Ralph Workflow" | Credited pattern in ghuntley.com/ralph lineage. |
| X2 | SHOWCASE.md Entry #2 | Listed under "Ralph Workflow Showcase" | Independent convergence, explicitly NOT a RW credit. |
| X3 | SHOWCASE.md Entry #3 | Listed under "Ralph Workflow Showcase" | Built own system, explicitly NOT a RW user. |
| X4 | Compare page Nightcrawler | "credits Ralph Loop" used as RW validation | Credits ghuntley.com/ralph, not ralph-workflow. FIXED 2026-06-16 22:20. |
| X5 | SHOWCASE.md Retracted | Nightcrawler credit was already retracted | Confirms systemic conflation problem across all surfaces. |
| X6 | Comparison-guide blog Nightcrawler | "strongest signal yet that the market has caught up to Ralph Workflow's thesis" | Fabricated framing. FIXED 2026-06-16 22:20. |
| X7 | vs-nightshift blog | "strongest independent validation yet that policy-enforced autonomous coding works at scale" | Fabricated framing. FIXED 2026-06-16 22:20. |
