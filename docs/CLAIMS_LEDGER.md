# Claims Ledger — Ralph Workflow Public Surfaces

> Every factual claim on every public-facing surface. Verified = ✅, False = ❌,
> Unverified = ⬜, Stale = ⏳, Conflated = 🔀.

**Rule:** No claim may appear on a public surface without a corresponding entry here
marked ✅ or with a `(source, date)` citation inline.

**Last audit:** 2026-06-16 22:00 GMT+2

---

## Audit Summary (2026-06-16)

| Category | Count |
|---|---|
| Total claims cataloged | ~60 |
| Verified true | 12 |
| False and fixed | 12 |
| Conflated (pattern≠product) | 9 (fixed in SHOWCASE.md + blog posts + Nightcrawler sections) |
| Stale and fixed | 1 |
| Unverified (open) | ~25 (mainly YC batch assignments) |

### Fabrication register (confirmed hallucinations)
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

### Verified true register
1. Emdash "206 HN points" → HN id 47140322 ✅
2. Emdash ~4,877 GitHub stars → generalaction/emdash ✅
3. Freestyle "322 HN points" → HN id 47663147 ✅
4. kodo exists → ikamensh/kodo, 107⭐, MIT ✅
5. aicom exists → alexar76/aicom, 1⭐, MIT ✅
6. 12,304 lifetime / 4,430 30d PyPI downloads → pepy.tech ✅
7. Bernstein 320,000+ total installs → pepy.tech ✅
8. umputun/ralphex exists → 1,274⭐, created Jan 2026 ✅
9. cvemprala/ralph-addons exists → 2⭐, created Jan 2026 ✅
10. daegwang/ralph-code exists → 4⭐, created Feb 2026 ✅
11. happycastle114/oh-my-openclaw exists → 181⭐, created Feb 2026 ✅
12. GitHub user "miserness" exists but has NO ralphy repo → verified via API ✅

---

## Surface: Compare Page (`ralphworkflow.com/compare`)

| # | Claim | Status | Evidence |
|---|---|---|---|
| C1 | ~~Continue "Acquired by Cursor · Jun 2026"~~ | ❌ FIXED | Fabricated. Replaced with "Read-only · Final rel. Jun 2026." |
| C2 | Continue read-only / no longer maintained | ✅ | Continue GitHub README |
| C4 | ~~Nightcrawler "credits Ralph Loop as predecessor" used as RW validation~~ | 🔀 FIXED | Now links to ghuntley.com/ralph as "the Ralph Loop pattern." Explicit: "several independent projects have converged on" it. No longer framed as RW validation. Fixed 2026-06-16 22:20. |
| C5 | Nightcrawler "Claude Code-specific, mac-only" | ⬜ | |
| C7 | Emdash "206 HN points" | ✅ | HN id 47140322, Show HN Feb 24 2026 |
| C8 | Emdash "200+ GitHub stars" | ✅ | Actual: 4,877⭐ (generalaction/emdash) |
| C9 | Twill "YC S25" | ⬜ | |
| C10 | Twill cloud-based coding delegation | ⬜ | |
| C11 | ~~Symphony "14 merged PRs from 20 issues in a four-day sprint"~~ | ❌ FIXED | Unverifiable "early-adopter reports." Removed. |
| C12 | ~~Symphony "open-source Elixir service"~~ | ❌ FIXED | Internal OpenAI tool. Community reimpls exist. Fixed. |
| C14 | ~~kodo "ottiwroteit"~~ | ❌ FIXED | Wrong. Actual: ikamensh/kodo (107⭐). Fixed. |
| C15 | kodo "Claude Code Max-dependent" | ✅ | README says "Claude Code Max subscription" |
| C16 | ~~Nightshift "935 tests, 80 merged PRs, 28 modules"~~ | ❌ FIXED | Fabricated. Repo has 0 stars. Fixed. |
| C17 | ~~Nightshift "by Orbit/Recusive"~~ | ❌ FIXED | Org doesn't exist. Fixed to ericmitchellchan/nightshift. |
| C18 | Nightshift "single-agent, Claude Code-focused" | ⬜ | Needs verification against actual repo. |
| C19 | Bernstein "320,000+ installs (pepy.tech, 2026-06-16)" | ✅ | Fixed from stale 169,000+. |
| C20 | ~~Bernstein "runs 46 CLI coding agents"~~ | ❌ FIXED | Real number: 44 (per PyPI). Also fixed Bernstein GitHub URL (was 404). Fixed 2026-06-16 22:20. |
| C21 | Bernstein "Apache 2.0" | ⬜ | |
| C22-26 | ~~aicom specific pipeline numbers~~ | ❌ FIXED | 61K LOC, 12 agents, 5 quality gates, 11-state, 34 transitions, 6+ providers — none in README. Removed. |
| C27 | Freestyle "322 HN points" (linked) | ✅ | HN id 47663147 verified. |
| C28 | Freestyle "YC P26" | ⬜ | In YC directory, batch unconfirmed. |
| C30 | Claude Code Routines "Anthropic, April 2026 research preview" | ⬜ | |
| C31 | "10,700+ lifetime / 4,000+ 30d PyPI (pepy.tech, 2026-06-12)" | ✅ | Conservative, sourced. |
| C32 | "19 tools" listed | ⬜ | Count not verified. |

---

## Surface: SHOWCASE.md (`/SHOWCASE.md` on Codeberg)

| # | Claim | Status | Evidence |
|---|---|---|---|
| S1 | ~~"Martingale42 credited Ralph Workflow"~~ | ❌ FIXED | Credited the pattern, not the product. Moved to Pattern Ecosystem. |
| S2 | ~~"gbrennon's Odysseus is a Ralph Workflow showcase entry"~~ | ❌ FIXED | Independent convergence, not product credit. Moved. |
| S3 | ~~"Adam Tuttle's blog is a Ralph Workflow showcase entry"~~ | ❌ FIXED | Built his own version, didn't use RW. Moved. |
| S4 | "Verified Ralph Workflow Credits: 0" | ✅ | Honest. |

---

## Surface: Blog Posts

| # | Claim | Status | Evidence |
|---|---|---|---|
| B1 | ~~Continue "Acquired by Cursor" (vs-continue)~~ | ❌ FIXED | Fabricated. Fixed 2026-06-16. |
| B2 | ~~Nightshift "by Orbit/Recusive, 935 tests, 80 PRs" (vs-nightshift)~~ | ❌ FIXED | Fabricated. Fixed 2026-06-16. |
| B3 | Freestyle "322 HN points" (goldrush post) | ✅ | HN id 47663147 verified. |
| B4 | ~~Freestyle/Hyper/Superset/Twill "YC P26" (goldrush + why-5 + vs-hyper posts)~~ | ❌ FIXED | Batch unconfirmed (Emdash is W26, Superset HN from Dec 2025). Fixed to "YC company." |
| B5 | ~~Hyper "launched on HN 2 days ago" (vs-hyper)~~ | ❌ FIXED | No HN launch found. Removed. |
| B6 | ~~miserness/ralphy exists (goldrush + why-5 posts)~~ | ❌ FIXED | User miserness exists, but NO ralphy repo. Fabricated. Removed 2026-06-16. |
| B7 | ~~"/cv/ralph-addons" (goldrush + why-5)~~ | ❌ FIXED | Wrong owner. Actual: /cvemprala/ralph-addons. Fixed. |
| B8 | ~~"Ralph-code (May 2026)" (why-5 post)~~ | ❌ FIXED | Created Feb 25, not May. Fixed. |
| B9 | ~~"Ralphex (April 2026)" (why-5 post)~~ | ❌ FIXED | Created Jan 19, not April. Fixed. |
| B10 | ~~"5 independent reinventions" count (goldrush + why-5)~~ | ❌ FIXED | Only 4 exist (ralphy fabricated). Fixed to "several." |
| B11 | "All 5 projects are Ralph Loop derivatives, not Ralph Workflow credits" (why-5) | 🔀 FIXED | Same conflation as SHOWCASE.md X1-X5. Disclaimer added 2026-06-16. |
| B12 | Twill "YC S25" (comparison-guide + why-local-first) | ⬜ FIXED | Unverified batch. Changed to "YC." 2026-06-16. |
| B13 | Other blog comparison posts (vs-aider, vs-claude-code, vs-cursor, vs-copilot, vs-hermes, vs-conductor-oss, vs-conductor-teams) | ⬜ | Not yet audited. No fabricated claims detected on quick scan. |

---

## Conflation Register (Ralph Loop ≠ Ralph Workflow)

Claims that treat pattern credit as product credit. All fixed in SHOWCASE.md 2026-06-16.

| # | Where | The Claim | Reality |
|---|---|---|---|
| X1 | SHOWCASE.md Entry #1 | "Peer-engineer credit of Ralph Workflow" | Credited pattern in ghuntley.com/ralph lineage. |
| X2 | SHOWCASE.md Entry #2 | Listed under "Ralph Workflow Showcase" | Independent convergence, explicitly NOT a RW credit. |
| X3 | SHOWCASE.md Entry #3 | Listed under "Ralph Workflow Showcase" | Built own system, explicitly NOT a RW user. |
| X4 | Compare page Nightcrawler | "credits Ralph Loop" used as RW validation | Credits ghuntley.com/ralph, not ralph-workflow. FIXED 2026-06-16 22:20. |
| X5 | SHOWCASE.md Retracted | Nightcrawler credit was already retracted | Confirms systemic conflation problem across all surfaces. |
| X6 | Comparison-guide blog Nightcrawler | "strongest signal yet that the market has caught up to Ralph Workflow's thesis" | Fabricated framing. FIXED 2026-06-16 22:20. |
| X7 | vs-nightshift blog | "strongest independent validation yet that policy-enforced autonomous coding works at scale" | Fabricated framing. FIXED 2026-06-16 22:20. |
