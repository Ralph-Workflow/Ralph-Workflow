<!-- AI AGENTS: DO NOT MODIFY. For crate docs, edit ralph-workflow/README.md. Ask a human before changing this file. -->

# Ralph Workflow

Ralph Workflow is a CLI orchestrator for AI coding agents that enables long-running, unattended development workflows—typically 30 minutes to several hours depending on task complexity.

You describe a feature in `PROMPT.md`, run `ralph`, and the system:

1. **Plans** the implementation
2. **Develops** the code
3. **Verifies** against the plan
4. **Inner loop**: Developer refines until the plan is satisfied
5. Loops back to step 1 for next iteration
6. **Commits** the results

```
┌─────────────────────────────────────────────────────────────┐
│                    Development Iteration                        │
│  ┌─────────┐     ┌──────────┐     ┌──────────┐              │
│  │  Plan   │────▶│ Develop  │────▶│ Analyze  │──┐           │
│  └─────────┘     └──────────┘     └──────────┘  │           │
│       ▲                                 │         │           │
│       └─────────────────────────────────┘           │
│              (refine until satisfied)                │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼ (next iteration)
                    ┌─────────┐
                    │ Commit  │
                    └─────────┘
```

You can configure different agents for each phase, enabling cost-effective strategies like using a powerful model for planning, a fast cheap model for development, and a reasoning model for verification.

**Ralph Workflow is a framework built around the original Ralph concept** by [Geoffrey Huntley](https://ghuntley.com/ralph/). While preserving the core philosophy of unattended loops, Ralph Workflow extends it with a structured **Plan → Develop → Verify** cycle that iterates until completion. This layered approach ensures high code quality through automated verification while maintaining the original goal of hands-off, long-running development workflows.

## Is This For You?

Ralph Workflow is **not meant to be babysat**. Unlike most active agent orchestrators, it won't ask you for clarification when there's ambiguity. You need to provide enough context in your `PROMPT.md` for Ralph to complete the work without additional input, or it will be forced to make assumptions you may not agree with.

If you're looking for an interactive orchestrator, you're probably looking for something else.

Ralph Workflow works best if you think like a Product Manager and can scope out every detail about the feature you need. The more details you add, the better it performs. It's meant for long-running, deterministic tasks that need many commits and a non-trivial amount of manual work.

## Prerequisites

Before installing Ralph Workflow, ensure you have:

- **Rust** - Install via [rustup](https://rustup.rs/)
- **Git** - For version control operations
- **An AI coding agent** - One of:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (recommended)
  - [OpenAI Codex CLI](https://github.com/openai/codex)
  - [OpenCode](https://github.com/opencode-ai/opencode)
- **API keys** configured for your chosen AI agent

## Quick Start

To install:

```bash
cargo install ralph-workflow
```

Then in your project, create a `PROMPT.md` file. Here's a simple example:

```markdown
== Goal

Users should be able to personalize greetings. Add a greeting feature that takes a name and returns a friendly hello message.
```

**Important:** PROMPT.md should describe *what* you want, not *how* to implement it. Focus on product definition and desired outcomes—let the AI figure out the implementation details. The more you specify implementation, the more you constrain the AI's ability to find good solutions.

For more complex tasks, provide detailed product specifications:

```markdown
== Goal

The test suite has tests that are tightly coupled to implementation details. Refactor the test suite to focus on black-box behavior testing. If code is untestable from the outside, refactor it to be testable.
```

Then run:

```bash
ralph
```

and watch the AI agent plan out and work through the refactor.

By default, ralph-workflow uses `claude` for all phases. To configure different agents for planning, development, and verification, see the Configuration section below.

For detailed usage, see the [Product README](ralph-workflow/README.md).

## Configuration

Ralph Workflow looks for config files in these locations:

| Scope | Location |
|-------|----------|
| Global (all projects) | `~/.config/ralph-workflow.toml` (Linux/macOS), `C:\Users\<username>\.config\ralph-workflow.toml` (Windows), or `$XDG_CONFIG_HOME/ralph-workflow.toml` if set |
| Local (per-project) | `.agent/ralph-workflow.toml` in your project root |

Local config overrides global config. Run `ralph --init-local-config` to create a project-local config.

Here's a minimal config example using agent chains with fallback logic:

```toml
[agent_chains]
# Planner chain: Claude for high-quality architectural decisions
planner = [
  "claude",      # Primary: Claude Code (best for planning/verification)
  "codex",       # Fallback
]

# Developer chain: cheaper models for implementation (plan already exists)
# Uses opencode/provider/model syntax for provider-specific agents
developer = [
  "opencode/minimax/m2-5",          # Primary: MiniMax M2.5 (cheap, capable)
  "opencode/zai-coding-plan/glm-5", # Fallback: GLM-5
]

[agent_drains]
planning = "planner"       # Uses planner chain (Claude)
development = "developer"  # Uses developer chain (cheap models)
analysis = "planner"       # Uses planner chain (Claude)
review = "planner"         # Uses planner chain (Claude)
fix = "developer"          # Uses developer chain
commit = "planner"         # Uses planner chain (Claude)
```

For a complete config with all options, see [ralph-workflow/examples/ralph-workflow.toml](ralph-workflow/examples/ralph-workflow.toml).

## Supported Agents

Ralph Workflow works with these CLI-based coding agents:

* **Claude Code** (including [Claude Code Switch](https://github.com/smithjr/claude-code-switch) for profile management)
* **OpenAI Codex CLI**
* **OpenCode** — supports many providers (MiniMax, GLM, OpenAI, Anthropic, Google, open-source models, etc.)

Since OpenCode is highly flexible, any model it supports is available through Ralph Workflow.

## Recommendations

* **Development is your biggest cost—use cheaper models there.** The development phase runs repeatedly but needs the least reasoning since the plan is already worked out. Use cheaper models like GLM-4, MiniMax, or open-source alternatives for development, and reserve top-tier models like Claude for planning and verification where architectural judgment actually matters. The cost savings compound quickly.
* **Make sure your PROMPT.md describes outcomes, not implementations—unless you're a software architect who understands the trade-offs.** Focus on product definition: what the feature should do, acceptance criteria, edge cases, and how it should behave. Generally, avoid prescribing specific algorithms, data structures, or code patterns—the AI will make better architectural decisions when given clear goals without implementation constraints. However, if you have strong architectural opinions and understand the trade-offs involved, it's perfectly valid to specify implementation details like "use event sourcing" or "prefer immutability." The key is knowing *why* you're constraining the solution space.

## Design Philosophy

Ralph Workflow is designed to make as many deterministic decisions as possible. The system passes structured XML between phases, telling the next agent exactly what it needs. It deterministically parses outputs and executes actions accordingly through a defined pipeline. It only calls upon an AI agent when it needs to make decisions about code.

## Questions

**Do I need coding knowledge to use this?**

Software engineering skills are more important than ever. AI can generate code, but it cannot replace the judgment of a skilled engineer. You need to recognize good code from bad, understand when technical debt is accumulating, evaluate architectural trade-offs, know when a feature needs foundational work first, and spot subtle bugs that tests won't catch. The AI handles the mechanics of coding—you provide the engineering judgment that determines whether the result is actually good. If you have those instincts, you can guide Ralph Workflow effectively even if you're not writing the code yourself.

**Should I use this in production-level code?**

Yes, with the same discipline you'd apply to any code review. Treat AI-generated code like code from a junior developer who works fast but needs supervision: review it thoroughly, run your test suite, check for edge cases, and verify it matches your architectural standards. The code isn't inherently worse than human-written code, but it requires the same scrutiny you'd give any pull request. If you wouldn't merge a human's PR without review, don't merge AI's either.

**Should I use Claude models?**

Yes—for planning and analysis. These phases need a model that can understand your entire codebase, reason through architectural trade-offs, and produce a solid plan. Claude's large context window and strong reasoning make it well-suited for this.

Development is different. Once a plan exists, cheaper models can follow it with minimal supervision—as long as your codebase has strong test suites, clear separation of concerns, and side effects contained to specific modules. Use Claude where reasoning matters (planning, analysis), and save money by using cheaper alternatives for development.

**What is the recommended workflow with this?**

I recommend using Ralph Workflow on different Git worktrees so you can work on multiple features at the same time. Due to its unattended nature, Ralph Workflow naturally takes longer than interacting with an AI agent directly. While you can run it on the main branch, it reduces your ability to work on multiple features simultaneously.

## Origin

I started this project as a side project with a bunch of shell scripts while working on my main project, testing out the concept. Then I decided I wanted it working on separate parts of the project like different worktrees. As a result, this became increasingly complex and I changed it to Rust (no, this isn't one of those rewrite-everything-in-Rust stories; shell scripts genuinely do not scale well in big codebases).

## About Me

I'm Mistlight, a Senior Software Engineer with over a decade of industry experience. I specialize in Software Engineering and AI, with a passion for solving AI workflows. 

## LICENSE

Licensed under AGPL-v3. No, this will not GPL or AGPL the code it generates. AGPL only applies to this codebase itself.
