# Fabrication Guardrails

> **MANDATORY PROTOCOL** for every contributor who edits public-facing
> markdown. The D91 fabrication incident (commit `58a1d25e9`,
> 2026-06-21) fabricated an entire `USERS.md` entry
> (`john-ezra/open-ralph`) — a nonexistent repo, npm package, and
> user. This was the second fabrication in the project (after the
> 2026-06-11 `SHOWCASE.md` failure). Fabrication is the single
> gravest threat to this project's credibility. The guard is
> unweakenable by design.

## Who this is for

Every contributor and reviewer of any public-facing markdown file in
this repository. If you edit, add, or approve a change to
`README.md`, `ralph-workflow/README.md`, `USERS.md` (the canonical
community directory after the 2026-07-07 docs cleanup),
`docs/`, or the Sphinx operator manual, this page is your contract.
The previous near-duplicate community surfaces (`SHOWCASE.md`,
`ECOSYSTEM.md`, `COMPARISONS.md`, `CREDIT_TEMPLATE.md`) were
deleted in the same cleanup; USERS.md is the single canonical
community surface.

## Read this first

- **[verification.md](verification.md)** — the verification workflow
  the fabrication guard is part of.
- **[AGENTS.md § Fabrication Guard](../../AGENTS.md)** — the
  short-form contract as it appears in the contributor root.

## Three-level defence

The guard system is a multi-level fabrication defense implemented in
[`scripts/fabrication_guard.py`](../../scripts/fabrication_guard.py):

- **Level 1 — Pattern detection (regex, no network, <100ms).** Catches
  known bad patterns: Nightcrawler misattribution, stale install
  counts, unverified npm package claims, bare star/download counts,
  and other patterns a fabricated claim leaves behind. Runs as a
  **pre-commit hook** (`.githooks/pre-commit`, wired via
  `git config core.hooksPath .githooks`; install by running
  `make setup-hooks` from the repo root) — the hook blocks
  any commit that fails Level 1.
- **Level 2 — Existence verification (network, cached, ~5s first
  run).** Verifies every GitHub repo URL, every npm package
  reference, and every external link in the public-facing markdown
  actually exists. Uses GitHub API and npm registry; results are
  cached in `.git/fabrication-cache.json`. Run with
  `--level 2`.
- **Level 3 — Quantitative claim verification (network,
  authenticated, ~30s).** Cross-references specific claims (star
  counts, fork counts, file line counts, issue numbers) against
  live GitHub API data. Requires `GITHUB_TOKEN`. Run with
  `--level 3`.

## Mandatory protocol — DO NOT SKIP

Run these four steps for every public-facing markdown edit:

1. **Before editing** — run the guard on the file you are about to
   change:

   ```bash
   ./scripts/fabrication_guard.py --level 1 <file>
   ```

   If the file already fails Level 1, do not proceed. The change
   needs a separate fix first.
2. **Adding NEW external references** — if your edit introduces a
   GitHub repo link, an npm package name, or any external URL you
   have not verified, also run:

   ```bash
   ./scripts/fabrication_guard.py --level 2 <file>
   ```

   Level 2 reaches out to GitHub and the npm registry. Cache the
   result; if a link is transiently unreachable, retry; if it is
   genuinely broken, fix the claim.
3. **After editing** — re-run Level 1 (and Level 2 if you added new
   external references). The pre-commit hook will block you if you
   forget, but running it explicitly catches the issue before the
   hook machinery does.
4. **If ANY level fails** — fix the claim, do **not** weaken the
   guard, do **not** use `--no-verify` to bypass it. Bypassing the
   guard is fabrication.

## Annotate every external reference

Every public-facing claim about adoption, credits, usage, or stats
MUST be verifiable from a third-party source.

- **GitHub repo links** in claim files (`USERS.md`,
  `README.md`) MUST carry a `verify: repo-exists` annotation.
- **npm package claims** MUST carry a `verify: npm-@org/pkg-exists`
  annotation verified against the npm registry.
- **Bare star/download/install counts** MUST be paired with a
  `(source, date)` pair (for example:
  `*10,700+ lifetime PyPI downloads (pepy.tech, 2026-06-12)*`).

## Banned forever

Phrases that misattribute credit to Ralph Workflow, or that quote a
bare star / download / install count without an attached source and
date, are banned from every public-facing surface because they were
either fabricated or wrong. If a future contributor wants to add a
specific banned-phrase example here, that change must go through the
same review process as any other claim edit — and the underlying
claim must be independently verifiable from a current third-party
source.

Concrete examples of fabrication that triggered bans on this
project:

- A specific public-facing claim that misattributed credit to Ralph
  Workflow in another project's README (the underlying credit
  belongs to `ghuntley.com/ralph`, not this project).
- A specific bare PyPI install quote that was both stale and
  fabricated (no source, no date, no third-party verification
  possible).

The lesson is the same in every case: do not write a public-facing
adoption, credit, or stats sentence that is not independently
verifiable from a current third-party source at the moment you
write it.

## Why the guard is unweakenable

The guard is a hard invariant, not a configurable check:

- There is no whitelist of "safe" files — every `.md` file is in
  scope.
- There is no per-file opt-out — exemption requires updating
  `EXEMPT_FILES` in `scripts/fabrication_guard.py` and submitting
  that change through review.
- The pre-commit hook blocks any commit that fails Level 1.
- The CI gate runs the make-verify path, which the pre-commit hook
  protects; `make docs` is now a `make verify` prerequisite (see
  [verification.md](verification.md)).

## Next click

- [Verification workflow](verification.md)
- [Documentation rubric](../../docs/code-style/documentation-rubric.md)
- [`scripts/fabrication_guard.py`](../../scripts/fabrication_guard.py)

## Primary repo

- Codeberg (primary):
  <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- GitHub (read-only mirror):
  <https://github.com/Ralph-Workflow/Ralph-Workflow>
