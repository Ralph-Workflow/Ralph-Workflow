# AI Agent Workflow Composer: What It Actually Means and Why the Distinction Matters

Most tooling talk treats "AI agent workflow composer" as a fancy way of saying "prompt chain." They are not the same thing.

A prompt chain passes output from one model call into the next. A workflow composer defines what each agent phase owes the next, what the finish contract looks like, and how cost and quality get traded off along the way. That distinction is not academic — it is the difference between a run that hands you back a transcript and a run that hands you back something you can actually review.

## What a Workflow Composer Does That Prompt Chains Don't

**It treats completion as a state, not a feeling.** Prompt chains end when the model says they are done. A workflow composer ends when a defined artifact exists: a diff, a test bundle, a short receipt naming what changed and what still needs a human call.

**It routes across model families.** Claude Code for planning, Codex for implementation, OpenCode for review — not because one is smarter, but because the work shape matches the model's strengths at that phase. A prompt chain picks one model and stays there.

**It defines the handoff.** This is where most overnight runs actually fail. Nobody wrote down what phase one owes phase two. The agent that ran first thinks it finished. The agent that runs second starts from scratch. A composer makes the handoff explicit before either agent starts.

**It enforces cost boundaries.** A workflow composer can set a hard token budget per phase. A prompt chain runs until it stops, which in unattended scenarios means running up costs until something external cuts it off.

## The Structure in Practice

A Ralph Workflow compose file (TOML) looks like this:

```toml
[workflow]
name = "overnight-code-review"
max_cost_per_phase = 0.50

[phase.plan]
agent = "claude-code"
prompt = "Read SPEC.md. Flag any ambiguous requirements. Do not write code yet."

[phase.implement]
agent = "codex"
prompt = "Implement the spec. Run tests. Stop if cost exceeds {{phase.max_cost}}."

[phase.review]
agent = "opencode"
prompt = "Read the diff. Run the test suite. Report what passed, what failed, what looks wrong."
```

The output after each phase is a structured artifact, not a transcript. You can read the diff, check the test results, and decide whether to continue — without re-running the agent.

## What "Composer" Emphasizes

The word "composer" is doing real work here. A composer does not just execute a sequence — it arranges parts into a coherent whole, balancing tension between sections, enforcing transitions, and producing an output that makes sense as a complete piece rather than a series of disconnected steps.

For AI agent workflows, that means:

- Each phase has a defined input contract (what it receives from the previous phase)
- Each phase has a defined output contract (what it must produce before the next phase starts)
- The composer enforces the sequence and the transitions, not the model

## When a Workflow Composer Is Overkill

If you are running a single prompt and checking the output, you do not need a composer. The overhead is not worth it.

The workflow composer model pays off when:

- The task is too large to hold in a single context window
- Multiple agents with different strengths need to collaborate
- The run will happen unattended (overnight, over a weekend)
- You need to come back to something reviewable, not a chat log
- Cost control matters and you need hard boundaries per phase

## The Finish Line Problem

The most common failure mode in AI coding workflows is treating "the agent said done" as a finish line. It is not. The agent said done when it ran out of things to say, not when the work met a standard.

A workflow composer solves this by defining the finish line before the run starts. Done means: diff exists, tests passed or failures are explained, open questions are listed. Anything short of that is not done — it is paused.

## Try It

[Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) is a free open-source CLI that works as a workflow composer for Claude Code, Codex, and OpenCode. It runs on your own machine, defines phases and transitions in TOML, and produces reviewable artifacts instead of transcripts.

The next time you set up an overnight run, ask yourself: what does my agent owe the next phase, and how will I know if it delivered? If you do not have a good answer, you need a composer — not a better prompt.
