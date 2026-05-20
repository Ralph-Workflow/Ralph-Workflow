# Which Agent Should I Start With?

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow brings back a **strong software result** in your repo — diff, checks, artifacts — instead of a transcript and a claim that the task is done.

Why use it now? You do **not** need to switch your whole toolchain first. Pick one agent you already trust, run one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The short answer

For a first Ralph Workflow run, start with the agent that is **already installed, already authenticated, and already familiar** on your machine.

Do **not** optimize this choice too hard. The main first-run question is not "which model is theoretically best?"

It is:

> **Can I get one real unattended run to finish with a strong software result?**

If one agent is already working for you today, that is usually the right first choice.

## Fast decision guide

### Start with Claude Code if...

- `claude` already works on your machine
- you want the most straightforward documented path
- you care most about planning quality and clean review handoff

Why this is a good first fit:
- strong default for end-to-end unattended work
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
- you already have an OpenCode setup you like

Why this is a good first fit:
- preserves your existing gateway setup
- lets Ralph Workflow orchestrate the agent stack you already use
- good for teams that switch models often

## Best first-run rule

Pick the path with the **least setup friction**:

1. the agent is already installed
2. the agent is already authenticated
3. you have already used it manually in the same environment

If two agents are equally ready, prefer the one you would be happiest reviewing output from tomorrow morning.

## What matters more than the agent choice

For a first run, these matter more than whether you picked Claude Code, Codex, or OpenCode:

- choosing a **small real backlog task**
- writing a **clear one-paragraph spec** in `PROMPT.md`
- making sure the repo is safe to test in
- judging the result with one question: **does the implementation hold up?**

A good first task will teach you more than switching agents three times.

## What not to do

Avoid these first-run traps:

- picking an agent you have **not** installed yet if another one is already working
- trying to compare multiple providers before you have seen one successful handoff
- choosing a broad risky task just because the model feels strong
- treating the first run like a benchmark instead of an honest workflow test

## If you do not have any agent set up yet

Ralph Workflow does not replace the coding agent itself. Install and authenticate **one** supported agent CLI first, then come back and use Ralph Workflow to orchestrate it.

If you want the shortest honest path after that:

- [../START_HERE.md](../START_HERE.md)
- [first-task-prompt-templates.md](./first-task-prompt-templates.md)
- [when-unattended-coding-fits.md](./when-unattended-coding-fits.md)

If you want to inspect the project before you install it, start with the [primary Codeberg repo](https://codeberg.org/RalphWorkflow/Ralph-Workflow) and **star or watch it there** if it matches the kind of overnight handoff you want. The [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) is there too.

## Recommended first-run sequence

1. Pick the agent that is already working on your machine.
2. Use [../START_HERE.md](../START_HERE.md) for the fastest real-task path.
3. If `PROMPT.md` is blank, use [first-task-prompt-templates.md](./first-task-prompt-templates.md).
4. Run one bounded backlog task.
5. Review the result tomorrow and ask: **does the implementation hold up?**

That is enough to tell you whether Ralph Workflow is useful in your real environment.

## Turn the result into one public Codeberg action

If this page helped you pick the first agent path, the next step should keep the trust signal on the primary repo.

- **Inspect the primary repo on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star or watch on Codeberg** if the first run looks worth repeating: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or missing proof on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
- **Need the post-run scorecard first?** Read [After Your First Ralph Workflow Run](./after-your-first-run.md)
