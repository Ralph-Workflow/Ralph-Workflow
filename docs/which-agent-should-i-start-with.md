# Which Agent Should I Start With?

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to bring back a **reviewable result** in your repo instead of just a transcript and a claim that the task is done.

Why use it now? Because you do **not** need to switch your whole toolchain first. Pick one agent you already trust, run one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The short answer

For a first Ralph Workflow run, start with the agent that is **already installed, already authenticated, and already familiar** on your machine.

Do **not** optimize this choice too hard.

The main first-run question is not "which model is theoretically best?"
It is:

> **Can I get one real unattended run to finish with a reviewable result?**

If one agent is already working for you today, that is usually the right first choice.

## Fast decision guide

### Start with Claude Code if...

- `claude` already works on your machine
- you want the most straightforward documented path
- you care most about planning quality and clean review handoff

Why this is a good first fit:
- strong default choice for end-to-end unattended work
- commonly the clearest first-run path for Ralph Workflow users
- good when you want to judge the workflow, not compare providers yet

### Start with Codex if...

- you are already using OpenAI tooling
- you want an OpenAI-first setup
- you expect to care about cost control or provider familiarity more than picking one "best" agent

Why this is a good first fit:
- strong option for teams already standardized on OpenAI
- solid review and implementation choice
- keeps the first run inside tools you already know

### Start with OpenCode if...

- `opencode` already works on your machine
- you want multi-provider flexibility from the start
- you already have an OpenCode setup you like and do not want to reconfigure everything just to try Ralph Workflow

Why this is a good first fit:
- preserves your existing gateway setup
- lets Ralph Workflow orchestrate the agent stack you already use
- good for teams that switch models often

## Best first-run rule

Pick the path with the **least setup friction**.

That usually means:
1. the agent is already installed
2. the agent is already authenticated
3. you have already used it manually in the same environment

If two agents are equally ready, prefer the one you would be happiest reviewing output from tomorrow morning.

## What matters more than the agent choice

For a first run, these matter more than whether you picked Claude Code, Codex, or OpenCode:

- choosing a **small real backlog task**
- writing a **clear one-paragraph spec** in `PROMPT.md`
- making sure the repo is safe to test in
- judging the result with one question: **would I merge this?**

A good first task will teach you more than switching agents three times.

## What not to do

Avoid these first-run traps:

- picking an agent you have **not** installed yet if another one is already working
- trying to compare multiple providers before you have seen one successful handoff
- choosing a broad risky task just because the model feels strong
- treating the first run like a benchmark instead of an honest workflow test

## If you do not have any agent set up yet

Ralph Workflow does not replace the coding agent itself.

Before your first run, install and authenticate **one** supported agent CLI first.
Then come back and use Ralph Workflow to orchestrate it.

If you want the shortest honest path after that, continue with:

- [../START_HERE.md](../START_HERE.md)
- [first-task-prompt-templates.md](./first-task-prompt-templates.md)
- [when-unattended-coding-fits.md](./when-unattended-coding-fits.md)

## Recommended first-run sequence

1. Pick the agent that is already working on your machine.
2. Use [../START_HERE.md](../START_HERE.md) for the fastest real-task path.
3. If `PROMPT.md` is blank, use [first-task-prompt-templates.md](./first-task-prompt-templates.md).
4. Run one bounded backlog task.
5. Review the result tomorrow and ask: **would I merge this?**

That is enough to tell you whether Ralph Workflow is useful in your real environment.