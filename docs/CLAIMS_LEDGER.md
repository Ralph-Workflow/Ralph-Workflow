# Claims Ledger — Ralph Workflow Public Surfaces

> Every factual claim on every public-facing surface. Verified = ✅, False = ❌,
> Unverified = ⬜, Stale = ⏳, Conflated = 🔀, Removed = 🗑️.
>
> **Rule:** No claim may appear on a public surface without a corresponding entry here
> marked ✅ or with a `(source, date)` citation inline.

**Last audit:** 2026-06-30

---

## Audit Summary (2026-06-30)

| Category | Count |
|---|---|
| Total claims cataloged | 12 |
| Verified true | 10 |
| False and fixed (historical) | 12 |
| Conflated (pattern≠product) | 7 |
| Removed / no longer on public surface | 16 |
| Unverified (open) | 0 |

The 2026-06-30 refresh reconciled all open unverified entries against the
current repository public surfaces. Claims that only appeared on older
website/blog surfaces and are no longer asserted in this repo were marked
🗑️. The current surfaces (`README.md`, `START_HERE.md`, `SHOWCASE.md`,
`USERS.md`, `ECOSYSTEM.md`, `COMPARISONS.md`) use live repo links with
`(verify: repo-exists)` annotations; numeric claims such as star counts are
rendered live from the source repo pages rather than stated inline.

---

## Fabrication register (confirmed hallucinations — historical record)

These claims were identified and removed during prior audits. They are listed
here as a historical guard, not as live public claims.

1. **Continue "Acquired by Cursor · Jun 2026"** — zero evidence. FIXED 2026-06-16.
2. **Nightshift "by Orbit/Recusive" + "935 tests, 80 merged PRs, 28 modules"** — org doesn't exist on GitHub, repo is a 0-star test project. FIXED 2026-06-16.
3. **kodo "ottiwroteit"** — wrong owner. Actual: ikamensh/kodo. FIXED 2026-06-16.
4. **aicom specific pipeline numbers (61K LOC, 12 agents, 5 quality gates, 11-state, 34 transitions, 6+ providers)** — none appear in README or verifiable source. FIXED 2026-06-16.
5. **Symphony "14 merged PRs from 20 issues in a four-day sprint"** — sourced from unverifiable "early-adopter reports." FIXED 2026-06-16.
6. **SHOWCASE.md entries #1-#3** — all conflated pattern convergence with product credit. FIXED 2026-06-16.
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

### START_HERE.md (`/START_HERE.md`)

| # | Claim | Status | Evidence |
|---|---|---|---|
| S1 | Same autopilot positioning as README | ✅ | Mirrors README |
| S2 | `pipx install ralph-workflow` install path | ✅ | Documented inline |
| S3 | First-run command sequence | ✅ | Documented inline |

### SHOWCASE.md (`/SHOWCASE.md`)

| # | Claim | Status | Evidence |
|---|---|---|---|
| SC1 | Featured projects exist and implement Ralph Loop | ✅ | Each row has `(verify: repo-exists)` |
| SC2 | "Verified Ralph Workflow Credits: 0" | ✅ | Honest count |
| SC3 | 46+ projects across USERS.md and ECOSYSTEM.md | ✅ | Live repo links; count refreshed 2026-06-30 |

### USERS.md (`/USERS.md`)

| # | Claim | Status | Evidence |
|---|---|---|---|
| U1 | Listed pattern-implementation repos exist | ✅ | Each row has `(verify: repo-exists)` |
| U2 | Listed shadow-ecosystem repos exist | ✅ | Each row has `(verify: repo-exists)` |
| U3 | awesome-ralph community hub exists | ✅ | `(verify: repo-exists)` |
| U4 | Ralph Loop pattern attributed to Geoffrey Huntley | ✅ | Links to ghuntley.com/ralph |

### ECOSYSTEM.md (`/ECOSYSTEM.md`)

| # | Claim | Status | Evidence |
|---|---|---|---|
| E1 | 46+ projects across USERS.md and ECOSYSTEM.md | ✅ | Live repo links; count refreshed 2026-06-30 |
| E2 | Listed active community projects exist | ✅ | Each row has `(verify: repo-exists)` |
| E3 | Listed "in the wild" integrations exist | ✅ | Each row has `(verify: repo-exists)` |
| E4 | awesome-ralph community hub exists | ✅ | `(verify: repo-exists)` |
| E5 | Ralph Loop pattern attributed to Geoffrey Huntley | ✅ | Links to ghuntley.com/ralph |

### COMPARISONS.md (`/COMPARISONS.md`)

| # | Claim | Status | Evidence |
|---|---|---|---|
| C1 | Compared tools' repos exist | ✅ | Each row has `(verify: repo-exists)` |
| C2 | 46+ projects in ecosystem | ✅ | Cross-reference to USERS.md / ECOSYSTEM.md |
| C3 | Star counts rendered live from source repo pages | ✅ | Documented inline; audited 2026-06-30 |

---

## Historical surfaces (no longer maintained in this repo)

### Surface: Compare Page (`ralphworkflow.com/compare`)

🗑️ The local `COMPARISONS.md` was rewritten on 2026-06-30. The legacy compare-page
claims about Continue, Nightcrawler, Emdash, Twill, Symphony, kodo, Nightshift,
Bernstein, aicom, Freestyle, and Claude Code Routines are no longer asserted in
this repository. They are retained in the Fabrication Register and Verified True
Register above for historical context only.

### Surface: Blog Posts

🗑️ The blog-post claim rows (B1-B13) tracked claims in external publication
posts. Those posts are not part of the maintained repository public surfaces and
the specific claims are no longer asserted here. Past fixes are recorded in the
Fabrication Register and Conflation Register.

---

## Conflation Register (Ralph Loop ≠ Ralph Workflow)

Claims that treated pattern credit as product credit. All fixed in SHOWCASE.md 2026-06-16.

| # | Where | The Claim | Reality |
|---|---|---|---|
| X1 | SHOWCASE.md Entry #1 | "Peer-engineer credit of Ralph Workflow" | Credited pattern in ghuntley.com/ralph lineage. |
| X2 | SHOWCASE.md Entry #2 | Listed under "Ralph Workflow Showcase" | Independent convergence, explicitly NOT a RW credit. |
| X3 | SHOWCASE.md Entry #3 | Listed under "Ralph Workflow Showcase" | Built own system, explicitly NOT a RW user. |
| X4 | Compare page Nightcrawler | "credits Ralph Loop" used as RW validation | Credits ghuntley.com/ralph, not ralph-workflow. FIXED 2026-06-16 22:20. |
| X5 | SHOWCASE.md Retracted | Nightcrawler credit was already retracted | Confirms systemic conflation problem across all surfaces. |
| X6 | Comparison-guide blog Nightcrawler | "strongest signal yet that the market has caught up to Ralph Workflow's thesis" | Fabricated framing. FIXED 2026-06-16 22:20. |
| X7 | vs-nightshift blog | "strongest independent validation yet that policy-enforced autonomous coding works at scale" | Fabricated framing. FIXED 2026-06-16 22:20. |
