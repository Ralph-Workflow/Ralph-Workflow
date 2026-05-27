# Tomorrow-Morning Scorecard

> **Codeberg is the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> 
> **GitHub is the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Use this after your first Ralph Workflow run.

The goal is not to decide whether the agent sounded smart.
The goal is to decide whether the result is good enough to keep.

## The only pass/fail question

**Would I merge this if a human teammate handed it to me this morning?**

If the answer is no, that is still useful signal. Tighten the task or the checks and run again.

## 10-minute scorecard

Give each row a quick score:

| Check | Pass if... | Score |
| --- | --- | --- |
| Task match | The diff clearly matches the task you wrote down. | 0 / 1 |
| Scope control | Unrelated files or surprise cleanup did not spill into the run. | 0 / 1 |
| Verification | The promised tests/build/lint/screenshots actually ran. | 0 / 1 |
| Reviewability | You can understand the result in one sitting without transcript archaeology. | 0 / 1 |
| Open risks | Remaining edge cases or judgment calls are named clearly. | 0 / 1 |
| Merge confidence | You would keep this output instead of redoing it manually. | 0 / 1 |

## How to interpret it

- **6/6** — strong first proof
- **4-5/6** — promising, but tighten the task or checks before the next run
- **0-3/6** — bad first-fit task or weak handoff; narrow the task and make verification more explicit

## Review order

1. Re-read the task contract.
2. Open the diff before the summary.
3. Check the exact verification evidence.
4. Look for shared-boundary risk: auth, billing, migrations, config, permissions, CI.
5. Decide: **would I merge this?**

## What to do next

- If the score is weak because the task was too broad, narrow the task.
- If the score is weak because checks were vague, rewrite the Checks section in `PROMPT.md`.
- If the score is strong, follow the project on **Codeberg** first and open an issue for any first-run friction.

## Related pages

- [Start here on one real task](../../START_HERE.md)
- [First-task guide](../../docs/first-task-guide.md)
- [Review bundle example](./review_bundle_example.md)
- [Review AI coding output before merge](../guides/review_ai_coding_output_before_merge.md)
