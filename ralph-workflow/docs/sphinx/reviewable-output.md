# What Good Ralph Workflow Output Looks Like

Ralph Workflow only earns trust if the run gives you something you can evaluate like real software work, not just like agent theater.

Before you install anything, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

If the first real run earns trust, keep the public next step on Codeberg:

- **Star or watch the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or proof gaps:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## The actual evaluation standard

A useful Ralph Workflow run should let you answer the same questions you would ask of any serious software change:

- does the software run?
- did the relevant tests pass?
- did any integration or end-to-end checks run where they should have?
- does the diff match the requested task?
- are remaining risks or open questions called out clearly?

If the run only gives you a vibe, a transcript, or a polished summary, it is not good enough yet.

## What proof should exist after a run

This section is not the product pitch.

Its job is narrower: after Ralph Workflow runs, what concrete proof should exist that the workflow actually did software work instead of producing agent theater?

A good result should make three things obvious:

- **what the workflow changed**
- **what executable behavior now works or failed honestly**
- **what verification actually ran**

For example:

```text
Task: Add empty-project-name validation to the CLI create flow

Changed files:
- cli/create.py
- tests/test_create.py

Checks run:
- create-flow unit tests
- lint / formatting checks if applicable
- any relevant integration checks if they exist

Executable result:
- CLI create flow rejects empty project names
- existing valid-name flow still works

Open questions:
- should reserved names also be rejected?
- should whitespace be trimmed before validation?
```

That is the standard: not “the agent seemed smart,” but **working software, real checks, and explicit remaining uncertainty**.

If those three things are not obvious, the workflow has not finished its job cleanly enough.

## The five-minute proof check

1. Run the executable path or inspect the actual behavior.
2. Check whether unit, integration, and other relevant verification actually ran.
3. Open the diff and confirm the changes match the task.
4. Read the open questions and remaining risk.
5. Ask one question: **did the workflow produce software I can trust enough to keep or merge?**

If the answer is obvious, the workflow did useful work.
If the answer is murky, the handoff is still too weak.

## What to read next

- Want the concrete artifact bundle? Read [example-review-bundle.md](./example-review-bundle.md)
- Want the post-run branch point? Read [after-your-first-run.md](./after-your-first-run.md)
- Want the merge checklist? Read [review-ai-coding-output-before-merge.md](./review-ai-coding-output-before-merge.md)
- Want the cleanest finish receipt? Read [what-a-good-ai-coding-finish-receipt-looks-like.md](./what-a-good-ai-coding-finish-receipt-looks-like.md)

## Keep the public action on Codeberg

After one real run, the healthiest outcomes are:

1. **Promising run** → star or watch the primary repo on Codeberg.
2. **Shaky run** → open a useful first-run or docs/proof issue on Codeberg.

Do not leave the evaluation private if Ralph Workflow proved something real.
