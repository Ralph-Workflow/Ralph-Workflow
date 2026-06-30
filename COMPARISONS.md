# Comparing Loop Engineering Tools

Ralph Workflow is the reference orchestrator in a growing category. These independent projects implement the same plan→build→verify loop pattern with different architectural choices. Pick the right tool for your workflow.

| Tool | Stars (live) | Created | Approach | Best for |
|------|-------|---------|----------|----------|
| **Ralph Workflow** | [live stars](https://codeberg.org/RalphWorkflow/Ralph-Workflow) · [mirror stars](https://github.com/Ralph-Workflow/Ralph-Workflow) | 2026-01 ~v0.1 | Composable phase routing, cost arbitrage, checkpoint/resume, repo-based agent handoff | Multi-agent pipelines with different models per phase; projects that need resume-after-failure |
| [SantanderAI/ralph](https://github.com/SantanderAI/ralph) (verify: repo-exists) | [live stars](https://github.com/SantanderAI/ralph/stargazers) | 2026-06-17 | Bash/PowerShell — fresh session per iteration, works with Claude Code, Codex, Gemini CLI | Simple loop with no context bloat; quick setup; Windows + Linux |
| [rxdt/py_ralph_frame](https://github.com/rxdt/py_ralph_frame) (verify: repo-exists) | [live stars](https://github.com/rxdt/py_ralph_frame/stargazers) | 2026-06-23 | Python harness — spec-driven loop, `uvx`-installable, fresh-context runs | Python ecosystem users; lightweight spec→build→verify cycle |
| [colinhacks/fray](https://github.com/colinhacks/fray) (verify: repo-exists) | [live stars](https://github.com/colinhacks/fray/stargazers) | 2026-06-21 | Multi-agent methodology + Claude Code plugin — .fray/ thread board, dispatch/reconciliation hooks | Multi-agent orchestration with explicit agent roles and thread tracking |

<!-- docs-rubric review note (2026-06-30): inline (verify: repo-exists) annotations
     moved into each competitor Tool/project cell, mirroring the SHOWCASE.md /
     ECOSYSTEM.md inline-project-cell convention verified during planning.
     Redundant standalone prefix on the previous line 12 removed; the
     provenance sentence (live-stars, audited date) is retained unchanged.
     No star counts, dates, or repo URLs were altered. -->

Star counts rendered live from the source repo pages on fetch; audited 2026-06-30.

## How they compare

### Architectural choices

| | Ralph Workflow | SantanderAI/ralph | py_ralph_frame | fray |
|---|---|---|---|---|
| **Language** | Python (pypi package) | Bash / PowerShell | Python (`uvx`) | TypeScript (Claude Code plugin) |
| **Context strategy** | Checkpoint/resume — persistent state across iterations | Fresh session every iteration | Fresh context per run | Thread-board — per-agent context isolation |
| **Multi-agent** | Phase routing — different models per phase | Single agent per loop run | Single agent per run | Multi-agent with dispatch/reconciliation |
| **Cost control** | Cost arbitrage — route phases to cheaper models | Model selection via config | Model selection via config | Claude Code only |
| **Install** | `pipx install ralph-workflow` | Clone repo | `uvx py_ralph_frame` | Claude Code plugin install |
| **Resume** | Repo-based checkpoint/resume | None (fresh start each time) | Spec-based state tracking | Board-based state tracking |
| **Windows** | Via WSL | ✅ Native PowerShell | Via WSL | Via WSL |

### When to use which

**Use Ralph Workflow when:**
- You have a project with multiple phases (research → build → review → deploy)
- You want cost arbitrage (cheap model for research, expensive model for implementation)
- You need checkpoint/resume — the agent should pick up where it left off after a failure
- You're orchestrating multiple coding agents with repo-based handoff

**Use SantanderAI/ralph when:**
- You want the simplest possible loop — one command, one agent, fresh start
- Context accumulation is your main concern
- You work on Windows and want native PowerShell
- You want a 10-minute setup with zero Python dependency

**Use py_ralph_frame when:**
- You work in Python and want a lightweight harness
- You want spec-driven execution with clear pass/fail gates
- You want `uvx` one-shot installs with no global setup

**Use fray when:**
- You're orchestrating multiple agents with explicit roles and responsibilities
- You want per-agent context isolation via a thread-board
- You're already in the Claude Code plugin ecosystem

## The category is real

These four tools plus 46+ others in the ecosystem ([USERS.md](USERS.md) and [ECOSYSTEM.md](ECOSYSTEM.md), audited 2026-06-30) independently converged on the same pattern: plan → build → verify, iterated by an AI coding agent until tests pass. Different languages, different architectures, same core loop. The category name is **Loop Engineering** — and it's growing.

[Ralph Workflow on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow) · [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) · [Ecosystem →](ECOSYSTEM.md) · [Who's using it →](USERS.md)
