# Changelog

This page tracks the operator-visible changes shipped in each ralph-workflow release.


This page is the manual entry point for Ralph Workflow release notes.

## Where the canonical changelog lives

The authoritative changelog for the maintained Python project is
`ralph-workflow/CHANGELOG.md`, shipped in the package source tree. The
canonical published URL — the same URL exposed by the PyPI
`[project.urls]` metadata — is:

<https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/CHANGELOG.md>

(verify: url-resolves)

That file is the only place that records what changed, when, and which
test locks each behavior. This manual page exists only to make that
file reachable from the manual's Reference index; it intentionally
does **not** mirror the changelog contents. The mirror would drift.

## How an entry is structured

Each entry in `ralph-workflow/CHANGELOG.md` follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Concretely:

- **One line per change** — the conventional-commit subject, the commit
  SHA, and the test file that pins the behavior. Multi-paragraph prose
  is pruned to a one-line summary; deeper context lives in the commit
  message and the docs.
- **Subject prefix** — `feat(...)`, `fix(...)`, `refactor(...)`,
  `docs(...)`, `chore(...)`, `test(...)` — matching the
  `ralph --generate-commit` subject style.
- **Group headers** — `### Added`, `### Changed`, `### Fixed`,
  `### Removed`, `### Documentation`. Stick to the Keep a Changelog
  vocabulary so the rendered file stays predictable.
- **Test reference** — name the test module or file (for example,
  `tests/test_verify_invariants.py`) so a reader can jump from the
  changelog to the regression that locks the behavior.
- **One section per release** — `## [Unreleased]` collects current
  work; each released version gets its own `## [X.Y.Z] - YYYY-MM-DD`
  section.

## Why only `[Unreleased]` today

`git tag --list` is currently empty, so there is no upstream anchor
to backfill historical `## [0.x.y]` sections from. Inventing version
sections without tag evidence would violate the AGENTS.md fabrication
guard. The `[Unreleased]` section collects every change since the
project started tracking the changelog; a future release will rename
it to the released version and open a fresh `[Unreleased]`. This is
intentional discipline, not an oversight — see the
[Keeping the changelog honest](../../CONTRIBUTING.md) note for the
project's stance on fabricated history.

## Semantic-version intent

Ralph Workflow uses semantic versioning to make upgrade decisions
mechanical:

| Bump | When | Example |
|------|------|---------|
| **MAJOR** (`x.0.0`) | Breaking API or behavior change. | Removing a documented CLI flag. |
| **MINOR** (`1.x.0`) | New backward-compatible feature. | A new agent transport in `[agents.*]`. |
| **PATCH** (`1.2.x`) | Backward-compatible bug fix. | A typo in a CLI help string. |

The release workflow — bumping `__version__`, building artifacts,
publishing to Test PyPI then production, and tagging — is documented
in [Release & Versioning](versioning.md). This page is the read-side
companion: where the entries live, how they are structured, and what
each bump means for an upgrade.

## How to read the changelog

1. Open the canonical URL above. The file renders as plain Markdown
   on Codeberg; no JS, no SPA shell.
2. Read top-to-bottom — the most recent changes appear under
   `## [Unreleased]`, then `## [X.Y.Z]` sections newest-first.
3. For each entry, follow the linked commit SHA on Codeberg to see
   the full diff and review comments. Follow the named test file to
   the regression that locks the behavior.
4. If the change is user-visible (a new flag, a default change, a
   removed surface), the commit message or the linked docs page
   carries the full story.

## How to add an entry

Maintainers add entries in the same commit that lands the change.
The short recipe:

1. Open `ralph-workflow/CHANGELOG.md`.
2. Add a one-line bullet under the matching `### Added` /
   `### Changed` / `### Fixed` / `### Removed` / `### Documentation`
   header inside `## [Unreleased]`.
3. Use the conventional-commit subject style (`feat(...)`, `fix(...)`,
   ...) and include the commit SHA and the test file that locks the
   behavior.
4. Open the release PR. The release itself renames `[Unreleased]` to
   the released version and opens a fresh `[Unreleased]` — see
   [Release & Versioning](versioning.md) for the full workflow.

## Related pages

- [Release & Versioning](versioning.md) — the cut-a-release workflow,
  Test PyPI / production PyPI, and tag management
- [CLI Reference](cli.md) — `ralph --generate-commit` and other flags
  that touch release surfaces
- [Getting Started](getting-started.md) — install path and first-run
  walkthrough