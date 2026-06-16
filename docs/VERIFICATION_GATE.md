# Verification Gate — Public Claims Enforcement

> This file is the PROCESS. It prevents hallucinated claims from reaching public surfaces.

## Gate Rules (hard — violation = blocked deploy)

### 1. Every numeric claim MUST have a `(source, date)` citation inline.
- Acceptable sources: pepy.tech, pypistats.org, GitHub API, HN item URL, tool's own README/repo, shields.io badge from a verifiable endpoint.
- Unacceptable: "common knowledge," "I've heard," "roughly," "approximately," any unsourced number.
- Example correct: `"10,700+ lifetime PyPI downloads (pepy.tech, 2026-06-12)"`
- Example violation: `"935 tests, 80 merged PRs"` — no source, no date.

### 2. Every status claim about a competitor MUST be verifiable from their own repo/docs.
- "Acquired by X" → requires press release, blog post, or official announcement from either party.
- "Read-only" / "archived" → requires visible banner or README statement on their repo.
- "YC P26" / "YC S25" → requires visible YC badge, YC directory listing, or launch post.
- "MIT license" / "Apache 2.0" → requires LICENSE file in their repo.
- "macOS-only" / "Claude Code-only" → requires visible constraint in their docs.

### 3. Attribution claims MUST distinguish Ralph Loop (pattern) from Ralph Workflow (product).
- "X credits Ralph Workflow" → X must mention `ralph-workflow` PyPI package or `codeberg.org/RalphWorkflow` by name.
- "X credits Ralph Loop" → X credits ghuntley.com/ralph pattern — this is NOT a Ralph Workflow credit and must be labeled as pattern-credit only.
- Conflating the two is a FALSE claim.

### 4. No claim may appear on a public surface without a CLAIMS_LEDGER.md entry marked ✅.
- ⬜ entries → surfaces must either add `(source, date)` or remove the claim.
- 🔀 entries → must be reclassified as pattern-credit with clear disambiguation, or removed.
- ❌ entries → must be removed from public surfaces immediately.

### 5. SHOWCASE.md entries must credit Ralph Workflow specifically, not the Ralph Loop pattern.
- A "showcase" entry means: a real builder used the `ralph-workflow` PyPI package and publicly acknowledged it.
- Pattern convergence ("same primitives, independent implementation") is NOT a showcase entry.
- Pattern convergence is valid content for a separate "Pattern Adoption" or "Ecosystem" page, clearly labeled as such.

---

## Pre-Deploy Check Script

The script at `scripts/verify_claims.sh` must pass (exit 0) before any deploy.

It checks:
1. `grep -rP '[0-9]+,\d{3}\+|[0-9]+\s+(tests|PRs|modules|agents|LOC|HN points)'` on all public surfaces → each hit must have `(source, date)` within 80 chars.
2. `grep -r 'acquired'` → each hit must have a CLAIMS_LEDGER.md ✅ entry.
3. `grep -r 'credits Ralph'` → each hit must NOT conflate pattern with product.
4. `grep -r 'asciinema\|JDnY0'` → must return zero hits (dead links).
5. SHOWCASE.md entries count: every entry must credit `ralph-workflow` PyPI package or Codeberg repo by name.

---

## Audit Schedule

- **Pre-deploy:** verify_claims.sh runs automatically.
- **Weekly:** full CLAIMS_LEDGER.md re-audit (re-verify stale claims, check for new surfaces).
- **On any new blog post or page:** claim extraction and ledger entry before deploy.

---

## Responsible Party

The marketer (this agent) owns this gate. No human handoff. No "I'll fix it later." 
If a deploy would ship an unverified claim, the deploy is blocked and the claim is fixed or removed.
