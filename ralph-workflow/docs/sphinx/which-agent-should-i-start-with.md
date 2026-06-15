# Which Agent Should I Start With?

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


Ralph Workflow is **the operating system for autonomous coding**: a **free and open-source composable loop framework and AI orchestrator** that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the workflow model: Ralph Workflow lets you start with a strong default software workflow and route different phases across agents instead of relying on one long coding session.

Why use it now? You do **not** need to switch your whole toolchain first. Pick one agent you already trust, run one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The short answer

For a first Ralph Workflow run, start with the agent that is **already installed, already authenticated, and already familiar** on your machine.

Do **not** optimize this choice too hard.

The main first-run question is not "which model is theoretically best?"
It is:

> **Can I get one real unattended run to produce working software, real checks, or an honest blocked state?**

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

### Start with Nanocoder if...

- `nanocoder` already works on your machine
- you want a local-first, multi-provider coding agent surface
- you want Ralph Workflow to manage MCP wiring around Nanocoder's documented `run` mode instead of replacing your existing setup
- your tasks are **short enough to complete within 50 tool calls** (see known limitation below)

Why this is a good first fit:

- preserves an existing Nanocoder workflow instead of forcing a tool switch
- keeps Nanocoder inside the same unattended Ralph Workflow phase workflow as the other supported built-ins
- good when you want an opt-in alternative to OpenCode without changing Ralph Workflow's default chain choices

**Known limitation — 50-turn cap:** Nanocoder's headless `run` mode (which Ralph Workflow uses) has a hardcoded limit of 50 conversation turns in `plain/conversation.js`. There is no CLI flag to raise this limit. Tasks that require more than 50 back-and-forth model/tool exchanges will hit this cap and exit with an error. The Ink (TUI) runtime has no such limit but requires a real TTY — it cannot be used headlessly via subprocess pipe. For long or complex tasks, prefer Claude Code, OpenCode, or Google Anti Gravity instead.

### Start with Google Anti Gravity if...

- `agy` already works on your machine
- you want Gemini-model-backed coding assistance
- you prefer a lightweight TUI-focused experience

Why this is a good first fit:

- Gemini-backed coding with strong context handling
- Ralph Workflow orchestrates it with the same MCP-controlled workflow used for other supported agents
- good when you want Google ecosystem integration

Verify the install end-to-end with `python -m ralph smoke-interactive-agy`. The run uses `agy/Claude Sonnet 4.6 (Thinking)` by default and honors AGY's 5m `--print-timeout`; allow up to 6 minutes for the live run. If the smoke exits non-zero with `AGY --print returned empty stdout: ...`, the upstream `agy` binary returned no stdout; check `~/.gemini/antigravity-cli/cli.log` for an exhausted individual API quota (`429 RESOURCE_EXHAUSTED`) or an unrecognized model ID. These are upstream AGY conditions, not Ralph Workflow regressions.

## Best first-run rule

Pick the path with the **least setup friction**.

That usually means:

1. the agent is already installed
2. the agent is already authenticated
3. you have already used it manually in the same environment

If two agents are equally ready, prefer the one you would be happiest reviewing output from tomorrow morning.

## What matters more than the agent choice

For a first run, these matter more than whether you picked Claude Code, Codex, OpenCode, Nanocoder, or Google Anti Gravity:

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

Ralph Workflow does not replace the coding agent itself.

Before your first run, install and authenticate **one** supported agent CLI first.
Then come back and use Ralph Workflow to orchestrate it.

If you want the shortest honest path after that, continue with:

- [Getting Started](getting-started.md)
- [First-Task Prompt Templates](first-task-prompt-templates.md)
- [Choose Your First Ralph Workflow Task](first-task-guide.md)

## Recommended first-run sequence

1. Pick the agent that is already working on your machine.
2. Use [Getting Started](getting-started.md) for the fastest real-task path.
3. If `PROMPT.md` is blank, use [First-Task Prompt Templates](first-task-prompt-templates.md).
4. Run one bounded backlog task.
5. Review the result tomorrow and ask: **does the implementation hold up?**

That is enough to tell you whether Ralph Workflow is useful in your real environment.

## Best public next step after you pick an agent

Once you know which agent you want to start with, keep the public project relationship on **Codeberg**:

- **Inspect the primary repo first:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star or watch on Codeberg if the first run looks promising:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Open first-run friction or docs issues on Codeberg if the run misses:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

That keeps qualified evaluation traffic, trust signals, and feedback attached to the primary repo instead of leaking them across mirrors.
