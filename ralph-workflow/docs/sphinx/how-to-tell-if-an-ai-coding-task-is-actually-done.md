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

# How to Tell if an AI Coding Task Is Actually Done

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to leave you with a **strong software result** — a real diff, checks, artifacts, and a short result summary — instead of just a transcript and a confident done claim.

Why read this now? Because an AI coding task is not done when the model sounds done. It is done when the result comes back in a shape you can review, verify, and decide whether you would merge.

## The fast test: done means mergeable or honestly blocked

A trustworthy finish state should answer five questions quickly:

1. **What changed?**
   - You should be able to name the files or surfaces that moved.
   - The scope should still match the task you actually handed off.

2. **What proof came back?**
   - Tests, lint, build, screenshots, artifact files, or other concrete evidence should exist.
   - "It works" is not proof.

3. **What is still uncertain?**
   - Open questions should be called out explicitly.
   - Hidden uncertainty is how "done" turns into cleanup work for the human reviewer.

4. **Would you merge it?**
   - If the answer is yes, the task is done enough to matter.
   - If the answer is no, the run is not done just because the agent stopped typing.

5. **If it is blocked, is the block legible?**
   - A good failed run still leaves a readable trail: what was attempted, what failed, and what should happen next.

That is the real standard: **mergeable or honestly blocked**.

## Red flags that the task is not actually done

Be skeptical if you see any of these:

- the summary is confident but the diff does not match the ask
- there are changed files but no meaningful verification evidence
- the run touched shared boundaries and nobody checked the merged state
- the agent produced a long transcript but no short result summary
- the result created obvious follow-up work that was never named
- you still need to reconstruct the whole night before you can judge anything

Those are signs that the task may be **stopped**, but not truly **done**.

## What "done" should look like tomorrow morning

A strong unattended coding result should hand back:

- a understandable diff
- changed files that match the brief
- checks that actually ran
- a short result summary
- artifacts you can inspect when needed
- explicit open questions or residual risk
- a review path that ends in one question: **does the implementation hold up?**

If you want the concrete review checklist, read [review-ai-coding-output-before-merge.md](./review-ai-coding-output-before-merge.md).

If you want to see the artifact shape first, open [example-review-bundle.md](./example-review-bundle.md).

## Why this matters for unattended coding

The problem with most "AI coding is done" claims is not that the model failed to write code.

It is that the finish line stayed fuzzy.

For small interactive work, you can often patch that with live supervision.
For overnight or unattended work, that does not scale.

You need the end state to be legible without replaying the entire session.
That is why Ralph Workflow is built around **repo-local output** and a **reviewable morning-after handoff**, not just raw logs.

## The honest first evaluation path

If you are deciding whether Ralph Workflow is worth trying, use this sequence:

1. Inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. Run one real bounded task on your own machine
3. Review the output with this question: **does the implementation hold up?**
4. Turn the outcome into one public Codeberg action:
   - promising run → **star or watch on Codeberg**
   - rough run → **open a Codeberg issue with the missing proof or friction**

Best next public actions:

- **Inspect / star / watch on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
