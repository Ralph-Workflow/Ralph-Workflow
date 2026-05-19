# What Good Ralph Workflow Output Looks Like

Ralph Workflow only earns trust if the morning-after handoff is easy to judge.

Before you install anything, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

If the first real run earns trust, keep the public next step on Codeberg:

- **Star or watch the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or proof gaps:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## The short handoff standard

A useful Ralph Workflow run should hand back a review surface that is boring to inspect:

- one bounded task
- a small set of changed files
- checks that actually ran
- open questions called out clearly
- enough context to decide **would I merge this?**

If the handoff still forces transcript archaeology, it is not good enough yet.

## The shape you should expect

A good morning-after result should look roughly like this:

```text
Task: Add empty-project-name validation to the CLI create flow

Changed files:
- cli/create.py
- tests/test_create.py

Checks run:
- create-flow unit tests
- lint / formatting checks if applicable

Open questions:
- should reserved names also be rejected?
- should whitespace be trimmed before validation?
```

That is the standard: not “the agent seemed smart,” but **a bounded diff, real checks, and a clear merge decision**.

## The five-minute review path

1. Open the diff.
2. Confirm the changed files match the task.
3. Check whether the relevant tests or verification actually ran.
4. Read the open questions.
5. Ask one question: **would I merge this?**

If the answer is obvious, the workflow did useful work.
If the answer is murky, the handoff is still too weak.

## What to read next

- Want the concrete artifact bundle? Read [example-review-bundle.md](./example-review-bundle.md)
- Want the longer first-task proof walk-through? Read [free-open-source-proof.md](./free-open-source-proof.md)
- Want the post-run branch point? Read [after-your-first-run.md](./after-your-first-run.md)
- Want the merge checklist? Read [review-ai-coding-output-before-merge.md](./review-ai-coding-output-before-merge.md)

## Keep the public action on Codeberg

After one real run, the healthiest outcomes are:

1. **Promising run** → star or watch the primary repo on Codeberg.
2. **Shaky run** → open a useful first-run or docs/proof issue on Codeberg.

Do not leave the evaluation private if Ralph Workflow proved something real.
