# Claude Code + Codex Workflow: Plan, Build, Review

If you are already using Claude Code and Codex together, the hard part is usually not getting code written.

It is getting a finish you can trust the next morning.

That usually breaks in one of four places:
- the task was never sharpened enough before implementation started
- the build step and the verification step got split across tools with no clean finish line
- one tool made assumptions the next tool silently inherited
- the final artifact was a transcript, not finished code you can judge quickly

That is the gap Ralph Workflow is built for.

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator for developers and technical teams doing ambitious software work that needs a structured workflow instead of a chat session.

## The workflow problem is not model quality first

A lot of AI coding discussion still collapses into model comparisons.

That misses the real pain.

Most teams do not get stuck because Claude Code or Codex cannot produce code. They get stuck because the work between planning, implementation, verification, and final review is still manual glue.

You can have a strong builder and a useful second opinion and still end up babysitting the run.

The better question is not which coding agent feels smartest.

The better question is: when you come back later, do you have something you would actually ship?

## A boring workflow that holds up better

A practical Claude Code + Codex workflow usually looks like this:

1. **Plan the task**
   - choose one bounded backlog item
   - write a one-paragraph spec
   - define what must stay unchanged
   - name the checks up front

2. **Build in an isolated run**
   - keep the scope narrow
   - avoid mixing multiple unrelated changes
   - keep the diff reviewable

3. **Verify before calling it done**
   - run the tests, lint, build, or other checks you named in the spec
   - record what passed, what failed, and what was fixed
   - call out any open questions explicitly

4. **Review the morning-after artifact**
   - inspect changed files
   - inspect the checks that actually ran
   - ask one question: **would I ship this?**

That is the workflow gap most teams feel when they say they are “using Claude Code and Codex together” but still do a lot of manual glue work.

## Copy-paste spec template

```md
Change:
[what should change]

Keep unchanged:
[what must stay stable]

Done means:
[observable outcome]

Checks:
[tests, lint, build, or other verification]
```

Example:

```md
Change:
Add one filter and CSV export to the billing history page.

Keep unchanged:
Do not change invoice creation or billing calculations.

Done means:
Users can filter billing history by date range and export matching rows to CSV.

Checks:
Relevant billing tests pass and any new billing-history tests pass.
```

## Where manual glue gets expensive

The common failure mode is not model quality.

It is that the workflow never clearly separates:
- task sharpening
- implementation
- verification
- final review

That is why worktrees alone are not enough.

They help isolate code changes, but they do not define done, run checks, or force a clear handoff.

## Where Ralph Workflow fits

Ralph Workflow keeps a simple loop core, then composes that core into planning, development, verification, and broader workflow loops with a strong default workflow for writing software.

Its job is not to replace Claude Code or Codex.
Its job is to make the whole loop more reviewable and extensible:
- sharpen the task before coding starts
- keep build and verification in the same workflow
- end with evidence, not just a claim
- give you a strong default workflow you can use as-is or build on top

That matters if your current tool stack already writes code, but still leaves you doing the important finish by hand.

## When not to use this

Do not start with:
- a vague product idea
- risky production surgery
- a multi-system rewrite
- any task where nobody agrees what done means

The first run should be real, but easy to judge.

## Why use it now

Because you can try the default workflow tonight on one real backlog task, judge the morning-after result honestly, and keep the default or build on top if it earns the next task.

Start on Codeberg first:
- https://codeberg.org/RalphWorkflow/Ralph-Workflow

GitHub is the mirror:
- https://github.com/Ralph-Workflow/Ralph-Workflow
