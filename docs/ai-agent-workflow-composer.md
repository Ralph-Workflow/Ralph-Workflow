# AI Agent Workflow Composer: When One Agent Session Stops Being Enough

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


Ralph Workflow is a **free and open-source** AI agent workflow composer for developers who want work that is **too big to babysit and too risky to trust blindly** to come back as a strong software result instead of a transcript.

If you are searching for an AI agent workflow composer, the real question is not whether a tool can call more than one model. It is whether the workflow can stay understandable while still covering planning, implementation, verification, review, and re-entry.

## What an AI agent workflow composer should actually compose

A real AI agent workflow composer should let you build more than one long prompt chain.

It should help you compose a workflow where:

- the task starts from a written spec instead of a vague prompt
- planning, coding, verification, and review can be separate phases
- the finish line is a reviewable diff plus checks, not a claim that the agent is done
- the workflow stays repo-native and inspectable on your own machine
- the default path is already useful before you invent custom glue

If it cannot do those things, it is closer to prompt choreography than workflow composition.

## Why developers start looking for one

The usual failure pattern is not lack of model power. It is workflow collapse:

- one agent session turns into a long scrollback nobody wants to review
- verification happens late or not at all
- retries become manual babysitting instead of a structured phase
- every new step adds more shell glue and more maintenance burden

That is the gap an AI agent workflow composer is supposed to close.

## Where Ralph Workflow fits

[Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) is built for that gap.

Its core loop stays simple, but the workflow around it is composable:

1. write the real task in `PROMPT.md`
2. route work through planning, implementation, verification, and review
3. keep artifacts and checkpoints in the repo instead of burying them in chat history
4. come back to a result you can inspect and judge with one question: **does the implementation hold up?**

That is why Ralph Workflow is better described as a workflow composer than a single-agent wrapper. The point is not just to run agents. The point is to structure the whole handoff so the morning-after result is usable.

## Best first evaluation path

1. Inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. Use [START_HERE.md](../START_HERE.md) for one real first run
3. Use [first-task-guide.md](./first-task-guide.md) and [review-ai-coding-output-before-merge.md](./review-ai-coding-output-before-merge.md) to judge the result honestly
4. Turn that result into one public next step with [after-your-first-run.md](./after-your-first-run.md)

Use GitHub only as the mirror if that is where you already track projects: <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Best next step on Codeberg if this workflow shape matches what you need

Do not leave the evaluation private.

Use **Codeberg** as the main public home:

- **Inspect the source on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star or watch on Codeberg if the workflow earns trust:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report workflow-composition friction or proof gaps on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

That keeps the trust signal and the feedback loop on the primary repo instead of splitting them across surfaces.

## Why try it now

Because Ralph Workflow is free and open source, works with the agents you already use on your own machine, and gives you a practical way to test a composable overnight workflow on one real backlog task tonight.

Run one real task, judge the morning-after handoff honestly, and then take exactly one public action on Codeberg:

- **promising run:** star or watch the repo
- **shaky run:** open the right issue on Codeberg