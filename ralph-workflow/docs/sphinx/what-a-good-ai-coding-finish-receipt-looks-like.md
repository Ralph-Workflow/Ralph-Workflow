# What a Good AI Coding Finish Receipt Looks Like

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to leave you with a **reviewable result** — a real diff, checks, artifacts, and a short finish receipt — instead of a transcript plus a confident done claim.

Why read this now? Because a lot of unattended AI coding pain is not in starting the run. It is in the morning-after question: **what changed, what passed, and what still needs my judgment?**

## A finish receipt should save you from reconstructing the whole night

If the only way to understand the run is to reopen the terminal transcript and replay everything, the handoff is still weak.

A strong finish receipt gives you a fast clean re-entry point:

- what the run changed
- what checks actually ran
- what stayed uncertain
- what needs a human decision before merge

That is the difference between "the agent said done" and "I can review this quickly and decide whether to merge it."

## The five things a useful finish receipt should contain

1. **The scope in one glance**
   - What task did this run think it was solving?
   - Which files or areas changed?
   - Did the result stay bounded enough to review in one sitting?

2. **A short list of real checks**
   - Which tests, lint steps, or builds actually ran?
   - Which ones passed?
   - Which ones were skipped, flaky, or still need manual confirmation?

3. **Open questions, not buried uncertainty**
   - Shared boundaries touched: schema, config, auth, interfaces, migrations, build wiring
   - Assumptions the run made but did not prove
   - Anything that still needs merged-state validation before trust

4. **A clean path back to the diff**
   - The receipt should point you toward the actual changed files and artifacts
   - You should not need to reverse-engineer the result from chat logs

5. **A human decision line**
   - What is ready to merge?
   - What is ready only for review?
   - What still needs follow-up?

If the receipt does those five jobs, the morning-after review gets boring in the best way.

## What a weak finish receipt looks like

Be skeptical if the handoff mostly says things like:

- "done"
- "tests passed" without showing which checks
- "implemented the requested changes" without naming the real scope
- a long transcript with no short re-entry summary
- no clear note about uncertainty on shared boundaries

That kind of handoff forces you to reconstruct the run yourself.

## Why this matters more when you run unattended or with multiple agents

The bigger the task, the less helpful a raw transcript becomes.

When you hand off work overnight — or split planning, coding, and review across different tools — you need the finish line to stay legible. A short finish receipt helps you judge:

- whether the output stayed on task
- whether the checks were meaningful
- whether a second agent actually reviewed something important
- whether the combined result still looks safe to merge

That is why Ralph Workflow focuses on **what comes back**, not just how many agents you ran.

## Fastest honest way to test this

Pick one real backlog task tonight.

Run Ralph Workflow with a task small enough to review in one sitting.

Tomorrow morning, open the diff and the finish receipt and ask:

> **Would I merge this?**

If the answer comes quickly, the handoff is doing its job.
If you still need to reconstruct the whole night, tighten the scope or the review path and run again.

## Turn a good finish receipt into the right public next step

If this is the kind of morning-after handoff you want, keep the real project relationship on **Codeberg**:

- **Inspect / star / watch on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

A clean finish receipt should lead to one honest public action:

- if the run earned trust, put the signal on **Codeberg**
- if the run exposed a gap, report that exact gap on **Codeberg**

## Next reading

- [Getting Started](getting-started.md) — shortest path to a real first run
- [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md) — five-minute merge check for the morning-after handoff
- [What Breaks First When You Run Multiple Coding Agents?](what-breaks-first-with-multiple-coding-agents.md) — why review/reconstruction pain usually breaks trust before Git does
- [Example Review Bundle](example-review-bundle.md) — inspect a public sample prompt, result notes, review feedback, and artifacts
- [After Your First Ralph Workflow Run](after-your-first-run.md) — short Codeberg-first scorecard for what to do after a promising or rough first run
