# First Task Guide: Pick One Backlog Task You Can Judge Honestly

> **Codeberg is the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> 
> **GitHub is only the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

The best first Ralph Workflow run is not the smallest possible demo.

It is one meaningful task that is:
- too big to babysit in one chat
- small enough to review in one sitting
- clear enough to verify
- safe enough to roll back

If your first run misses those four tests, you can easily blame the workflow for a task-selection mistake.

## 1. Pick the right shape

Choose a task like:
- one bounded feature slice
- one isolated refactor with tests
- one integration point with a visible finish line
- one backlog item with clear file boundaries and explicit non-goals

Good examples:
- add CSV export to one billing-history page
- add one validation rule to a create flow
- wire one webhook path with tests
- replace one duplicated helper path with a shared utility

Avoid as a first run:
- redesign the architecture
- rewrite multiple systems at once
- risky production surgery
- vague polish work
- anything where success is mostly subjective

If you want a concrete example, use [Example first task](../content/examples/first_task_example.md).

## 2. Write the task as a short contract

Use this template in `PROMPT.md`:

```md
Change:
[what should change]

Keep unchanged:
[what must stay stable]

Done means:
[observable outcome]

Checks:
[tests, lint, build, screenshots, or other verification]
```

Example:

```md
Change:
Add CSV export to the billing history page.

Keep unchanged:
Do not change invoice creation, billing calculations, or existing filters.

Done means:
Users can export the currently filtered billing-history rows to CSV from the page.

Checks:
Relevant billing tests pass, any new billing-history tests pass, and the app build succeeds.
```

## 3. Keep the first run reviewable

Before you start, ask:
- can I explain the finish line in one paragraph?
- would a bad result be easy to roll back?
- do I know which checks should run?
- could I decide "would I merge this?" in under ten minutes tomorrow?

If not, narrow the task.

## 4. Judge the result by evidence, not narration

The next morning, ignore how confident the agent sounded.

Check:
- does the diff match the task?
- did the promised checks actually run?
- is the output small enough to inspect?
- are open risks called out clearly?
- **would I merge this?**

If the answer is no, that is still useful signal. Tighten the task or the checks and run again.

## 5. Best follow-up path

After the first run:
- if the task was too broad, narrow it
- if the diff was hard to review, sharpen the boundaries
- if verification was weak, make the checks explicit in the spec
- if the result felt strong, star/watch/open issues on **Codeberg** first

## Related pages

- [Start here on one real task](../START_HERE.md)
- [Good unattended task vs bad one](../content/guides/good_unattended_task.md)
- [Unattended AI coding workflow](../content/guides/unattended_ai_coding_workflow.md)
- [Review AI coding output before merge](../content/guides/review_ai_coding_output_before_merge.md)
