# How to Review AI Coding Output Before You Merge

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to leave you with a **reviewable result** — a real diff, checks, artifacts, and a short finish receipt — instead of just a transcript and a confident done claim.

Why read this now? Because the real question is not whether an agent looked smart. It is whether the morning-after result is something you would actually trust enough to review and merge.

## The five-minute merge check

Open the result in this order:

1. **Open the diff first**
   - Do the changed files match the task you asked for?
   - Is the scope still small enough to review in one sitting?

2. **Read the finish receipt**
   - What changed?
   - What checks ran?
   - What stayed uncertain?
   - What still needs a human judgment call?

3. **Confirm the real checks**
   - Do not stop at a summary.
   - Look for the actual test, lint, or build outputs that matter for this repo.

4. **Review shared boundaries carefully**
   - Schemas, config, interfaces, migrations, auth, and build wiring break trust faster than isolated files.
   - If the task touched a shared boundary, make sure the merged state still holds up.

5. **Ask one question**
   - **Would I merge this?**

If that answer is quick and boring, the handoff is strong.
If you still need to reconstruct the whole night from logs, it is not.

## What a trustworthy handoff should contain

A useful unattended run should give you:

- a bounded diff
- changed files that match the ask
- checks that actually ran
- a short finish receipt instead of a long transcript
- artifacts you can inspect when you need more detail
- open questions called out clearly instead of hidden behind confidence

If you want to see a public example, open [example-review-bundle.md](./example-review-bundle.md).

## Red flags before you merge

Slow down if you see any of these:

- the diff is much broader than the task
- the result depends on a shared boundary nobody clearly owned
- the summary says tests passed, but you cannot find the real check trail
- the handoff explains too much but still does not tell you what changed
- the run looks locally fine, but nobody checked the merged state you would actually land
- the only proof is a transcript plus the agent saying it is done

These are the cases where "agent said done" and "safe to merge" diverge.

## The fastest honest first evaluation

If you are trying Ralph Workflow for the first time, use this review path on **one real backlog task**.

That is the right test because Ralph Workflow is not just about generating code. It is about making unattended work come back in a form you can judge honestly on your own machine.

Best first-task companions:

- [../START_HERE.md](../START_HERE.md) — shortest path to a real first run
- [first-task-prompt-templates.md](./first-task-prompt-templates.md) — copy-paste spec shapes for strong first runs
- [free-open-source-proof.md](./free-open-source-proof.md) — what good output should look like
- [what-breaks-first-with-multiple-coding-agents.md](./what-breaks-first-with-multiple-coding-agents.md) — shared-boundary drift, finish receipts, and clean re-entry

If the morning-after result gives you a clean diff, real checks, and a merge decision you can make quickly, Ralph Workflow is doing useful work.

If that is the evaluation path you want, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Best next public actions:
- **Inspect / star / watch on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
