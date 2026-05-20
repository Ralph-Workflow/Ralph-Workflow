# Contributing to Ralph Workflow

**Primary repo: [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)**  
GitHub mirror: [github.com/Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow)

Issues, PRs, and reviews are welcome on Codeberg first.

---

## Best first contribution after your first run

If Ralph Workflow was useful, the highest-signal public actions are on **Codeberg**:
- ⭐ **Star the repo** — the best signal you can give without writing code
- 👀 **Watch the repo** — follow progress and releases
- 🐛 **Open an issue** — report first-run friction, missing docs, or a task that failed in an interesting way

Stars and watches help future evaluators discover the project. Bug reports with reproduction steps make it better.

**👉 [Star Ralph Workflow on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)**

If your first run failed because the task contract was fuzzy, start by reading **[Spec-Driven AI Agent](./content/guides/spec_driven_ai_agent.md)** and include the sharper spec when you open the issue.

## What counts as contribution

Ralph Workflow is a workflow tool. Contributions don't have to be code.

**Strong contributions:**
- Reporting real usability friction (not just "this could be better")
- Opening issues with specific failure scenarios and reproduction steps
- Submitting a working spec for a task that the workflow handled badly
- Improving docs that were unclear during your first real run
- Showing where the spec/result gap broke trust in a real unattended run

**Weaker contributions (but still welcome):**
- Refactors that don't change behavior
- "I would use it if..." suggestions without a concrete task

## How to contribute

### Report a bug

1. Search existing issues first — check if it's already reported
2. Open on Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues
3. Include: what you tried, what happened, what you expected
4. Bonus: include the `PROMPT.md` and outcome files from your run

### Suggest a feature

Open an issue labeled "enhancement" on Codeberg. Explain the workflow problem, not just the feature.

### Code contributions

1. **Fork on Codeberg** (primary) or use the GitHub mirror
2. **Clone:** `git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git`
3. **Install dev deps:** `make dev` (from the `ralph-workflow/` subdirectory)
4. **Verify before submitting:** `make verify`
5. **Run tests:** `make test`

The policy-driven pipeline means most behavior changes belong in `.toml` files under `ralph/policy/defaults/`, not in runtime Python. Read `ralph/policy/defaults/pipeline.toml` before adding code.

### Documentation

- User-facing Markdown docs live alongside the code
- Sphinx docs (for the docs site) live in `docs/sphinx/`
- Keep docs/guides aligned with what the workflow actually does — not what it should do

## Contribution standards

- **One logical change per PR** — refactors and behavior changes in separate PRs
- **Tests required** for behavior changes to `ralph/pipeline/`, `ralph/phases/`, or `ralph/agents/`
- **make verify must pass** before opening a PR — it runs formatting, linting, and type checks
- **No `# type: ignore` without a narrow reason** — see `docs/agents/type-ignore-policy.md`
- **No breaking changes to the policy schema** without a migration path

## Workflow philosophy

Ralph Workflow is designed around a specific problem: handing off substantial work to an AI agent and getting back something reviewable, not just a transcript.

If your change makes the workflow harder to understand, harder to trust, or less reviewable — it's probably the wrong direction, regardless of whether it "works."

## License

By contributing, you agree your work is licensed under the project's AGPL-3.0 license.
