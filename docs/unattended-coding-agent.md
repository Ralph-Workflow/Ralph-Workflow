# Unattended Coding Agent: What It Should Actually Mean

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
That simple center composes into more complex workflows for substantial, well-specified repo work on your own machine with the agents you already use, and the default workflow is already strong enough to start with.

If you are looking for an unattended coding agent, the important question is not just whether a model can keep typing while you are away.
The important question is whether you can come back to software you can judge honestly.

## Unattended should still mean accountable

A useful unattended workflow should leave you with:

- a clear task boundary
- software that actually runs or verifies better than before
- checks that tell you something real
- enough structure that you can understand what happened without replaying everything

That is the standard Ralph Workflow is trying to meet.
It is not promising magic independence from engineering discipline.
It is promising a stronger way to organize serious work.

## When unattended use fits best

Ralph Workflow fits best when you have:

- a backlog task with a written finish line
- a repo with meaningful tests or validation
- enough scope that a simple chat session becomes awkward
- time to evaluate the result after the run

It fits worse when the task is vague, tiny, or dependent on constant mid-run steering.

## Why the default workflow matters

Most people should not have to build an orchestration system before they can judge one.
You should be able to start with the shipped path, learn how it behaves on a real task, and extend it later only if you need to.

## Where to go next

- for the shortest honest first run: [START_HERE.md](../START_HERE.md)
- for choosing a task worth running unattended: [first-task-guide.md](./first-task-guide.md)
- for the maintained manual: [Sphinx manual home](../ralph-workflow/docs/sphinx/index.rst)
- for user-goal routing inside the manual: [user-stories.md](../ralph-workflow/docs/sphinx/user-stories.md)
