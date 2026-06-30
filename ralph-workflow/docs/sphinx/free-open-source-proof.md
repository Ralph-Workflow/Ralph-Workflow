<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: rewrote the opening paragraph so the page leads with the
    canonical autopilot positioning language instead of the older "AI agent
    orchestrator built around a simple ... Ralph-loop core" lead category.
  - Why it belongs here: this page is part of the maintained Sphinx manual;
    it must agree with the README and the manual home so the product story
    is coherent across surfaces (rubric hard failure: surfaces fight each
    other).
  - What was pruned: nothing material; the page's page-specific argument is
    preserved.
  - How the route is clearer: the lead now matches the canonical autopilot
    framing used by the README and the manual home.
-->

# Free and Open-Source Proof: What Good Ralph Workflow Output Looks Like

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

The fastest honest way to evaluate Ralph Workflow is not to ask whether it generated code.

Ask whether it gave you something you would actually review and merge.

Before you install anything, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

If the first real run earns trust, keep the public next step on Codeberg:

- **Star or watch the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or proof gaps:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Example first task

**Task:** Add validation so the CLI rejects empty project names before creating files.

Why this is a good first task:
- narrow scope
- obvious expected behavior
- easy to verify
- low rollback risk

### One-paragraph spec

When a user runs the project creation flow, reject empty or whitespace-only project names before any files are created. Keep the rest of the flow unchanged. Add or update tests to cover the validation. Done means the CLI shows a clear error, no project is created for invalid input, and tests pass.

## Example review bundle

A useful unattended result should not just say "done." It should leave behind a small review surface you can inspect in a few minutes:

```
.
└── cli/
    └── create.py
    └── tests/
        └── test_create.py
    └── .agent/
        └── PLAN.md
        └── DEVELOPMENT_RESULT.md
        └── ISSUES.md              # only if review found problems
        └── FIX_RESULT.md          # only if a fix pass ran
        └── artifacts/
            └── plan.json
            └── development_result.json
            └── issues.json
            └── fix_result.json
```

### What you should be able to review quickly

#### Task
Add empty-project-name validation to the CLI create flow.

#### Changed files
- `cli/create.py`
- `tests/test_create.py`

#### What changed
- validation rejects empty and whitespace-only names before file creation
- the user gets a clear error instead of partial output
- existing valid-name behavior stays unchanged

#### Checks run
- create-flow unit tests
- lint or formatting checks if applicable

#### Open questions
- should reserved names also be rejected?
- should the input be trimmed before validation?

## What to open first in the morning

1. Open the diff and confirm the changed files match the task.
2. Read `.agent/DEVELOPMENT_RESULT.md` for the implementation summary.
3. Read `.agent/ISSUES.md` and `.agent/FIX_RESULT.md` if the review/fix loop ran.
4. Run the relevant tests yourself.
5. Ask one question: **does the implementation hold up?**

If that path feels fast and honest, Ralph Workflow did its job. If it feels murky, the handoff is still too weak.

## Final review question

**Would you merge this?**

If the answer is not obvious, the handoff is not clean enough yet.

If the answer is yes, do not leave the evaluation private — star or watch Ralph Workflow on Codeberg.

If the answer is no, open the missing-proof or first-run-friction issue on Codeberg.
