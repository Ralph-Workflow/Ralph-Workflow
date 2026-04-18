# AGENTS.md

## Scope

This repository’s maintained implementation is the Python package in `ralph-workflow/`.
Treat older Rust-oriented material elsewhere in the repo as legacy background unless a document explicitly says it was refreshed for Python.

## Source of truth

Use these first, in this order:
1. `PROMPT.md`
2. `ralph-workflow/CONTRIBUTING.md`
3. `docs/agents/verification.md`
4. `ralph-workflow/README.md`
5. Python source and docstrings under `ralph-workflow/ralph/`

If instructions conflict, follow the stricter one.

## Priorities

1. Fix surfaced issues immediately.
2. Keep the Python package correct and verified.
3. Prefer small, maintainable diffs over quick hacks.
4. Keep documentation and commands aligned with actual behavior.

## Non-negotiables

- Work in `ralph-workflow/` for code, tests, and verification.
- Fix any bug, lint failure, type failure, test failure, or warning you surface before moving on.
- Do not leave the repo in a broken state.
- Do not weaken checks to get green results.
- Update user-facing docs when commands, workflows, or behavior change.

## Required workflows

- Feature or bugfix: use the `test-driven-development` skill first.
- Debugging or failing verification: use the `systematic-debugging` skill first.
- Any test work: read `docs/agents/testing-guide.md` first.
- Any commit work: dogfood Ralph itself by using `ralph --generate-commit`.

## Commit rule

Every git commit in this repository must be created with `ralph --generate-commit`.

- Do not use `git commit -m`, `git commit --amend`, or any other direct git commit path.
- Stage the intended files first, then run `ralph --generate-commit` from `ralph-workflow/`.
- If `ralph --generate-commit` fails, stop and fix that problem instead of falling back to `git commit`.

This rule is mandatory so commit generation is continuously dogfooded in real development.

## Verification

Before completion, run the required checks from `docs/agents/verification.md`:

```bash
cd ralph-workflow
make verify
```

Verification passes only when all required checks succeed with no ERROR/WARNING diagnostics.
If verification fails, fix the issue and rerun it.

Run the extra smoke checks or focused tests from `docs/agents/verification.md` whenever the touched area requires them.

## Documentation and file hygiene

- Keep Markdown concise and current with the Python project.
- Do not create temporary Markdown files in the repo root or `docs/`.
- Put temporary files under `tmp/` at the repo root.

## External dependencies

Do not assume third-party API behavior.
Research order: Context7 first, then official docs.
