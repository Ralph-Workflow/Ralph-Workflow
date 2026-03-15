# Ralph Workflow

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/LICENSE)
[![Rust](https://img.shields.io/badge/rust-stable-orange.svg)](https://www.rust-lang.org/)

**Ralph Workflow is an unattended AI agent orchestrator for long-running development tasks.** Write a detailed specification in `PROMPT.md`, start Ralph, and walk away. It coordinates AI agents through multiple development iterations and review cycles, producing commits automatically.

Ralph works best when you think like a Product Manager: scope out every detail of the feature you need. The more detail in your specification, the better Ralph performs. It is designed to run for hours without babysitting.

Inspired by [Geoffrey Huntley's Ralph Workflow concept](https://ghuntley.com/ralph/).

## When to Use Ralph

**Ralph excels at:**
- Long-running feature implementations with detailed specifications
- Systematic refactoring workflows requiring multiple iterations
- Test suite generation with comprehensive review
- Documentation writing with multiple review passes
- Any task where you can write a detailed spec and let it run unattended

**Not ideal for:**
- Vague or undefined requirements (Ralph needs detailed specs)
- Simple one-off commands (use Claude Code directly)
- Real-time interactive debugging
- Tasks requiring human judgment at each step

## How It Works

Ralph runs a multi-phase workflow:

1. **Developer Phase**: AI agent implements your spec through multiple iterations
   - Creates `PLAN.md` internally from your `PROMPT.md`
   - Executes the plan and makes code changes
   - Evaluate the code change and see if requires more work
   - Goes back if necessary and then make more code change, repeat until the plan is met
   - Auto-commits after each iteration
   - Cleans up and repeats for configured iterations

2. **Review Phase**: AI reviewer checks quality and fixes issues
   - Reviews code and creates `ISSUES.md` with problems found
   - Developer agent fixes the issues
   - Repeats until no issues or max cycles reached

3. **Final Commit**: Generates a meaningful commit message via AI

Ralph workflow automatically cleans context of AI agent to ensure that no context pollution exists when AI agents is being ran. The idea
behind this is to ensure that context pollution makes the code quality worse, so we ensure review agent has no context on what was done except
for the diff and the current state of the code, same thing as dev agent vs planning agent.

All orchestration files (PLAN.md, ISSUES.md) are controlled by Ralph, not the AI agents. This ensures deterministic, reliable operation.

## Quick Start

### 1. Install

```bash
git clone https://codeberg.org/mistlight/RalphWithReviewer.git
cd RalphWithReviewer

# Install from source
cargo install --path ralph-workflow --locked

# Or build + install via Makefile
make install-local
```

Alternatively you can use cargo crate
```bash
cargo install ralph-workflow --locked
```

### 2. Install AI Agents

Install at least one AI agent:

| Agent | Install | Recommended Role |
|-------|---------|------------------|
| **Claude Code** | `bun install -g @anthropic/claude-code` | Developer |
| **Codex** | `bun install -g @openai/codex` | Reviewer |
| **OpenCode** | See [opencode.ai](https://opencode.ai) | Either |

### 3. Run Ralph

```bash
# Create config file (smart init detects what you need)
ralph --init

# Navigate to your git repo
cd /path/to/your/project

# Create PROMPT.md from a Work Guide
ralph --init feature-spec
# Edit PROMPT.md with detailed requirements

# Run Ralph and walk away
ralph
```

### Work Guides

Work Guides are templates for describing your tasks to the AI. Use them with `--init`:

```bash
# See all available Work Guides
ralph --list-work-guides

# Create PROMPT.md from a Work Guide
ralph --init bug-fix              # Bug fix with investigation guidance
ralph --init feature-spec         # Comprehensive product specification
ralph --init refactor             # Code refactoring
ralph --init quick                # Quick/small changes
ralph --init test                 # Test writing

# Overwrite existing PROMPT.md
ralph --init bug-fix --force-overwrite
```

**Note:** Work Guides (for PROMPT.md) are different from Agent Prompts (backend AI behavior). Run `ralph --extended-help` for details.

## Writing Effective Specifications

Your `PROMPT.md` should be detailed. Example:

```markdown
# Task: Refactor Auth Module

## Description
Refactor the authentication module to use OAuth2 instead of basic auth.

## Requirements
1. Use passport-oauth2 library
2. Support GitHub and Google providers
3. Maintain backward compatibility with API keys
4. Add comprehensive tests

## Files to Update
- src/auth/mod.rs
- src/auth/oauth.rs (new)
- tests/auth_test.rs

## Constraints
- No breaking changes to public API
- All existing tests must pass
```

## Common Commands

### Preset Modes (control thoroughness)

```bash
ralph -Q              # Quick: 1 dev + 1 review
ralph -U              # Rapid: 2 dev + 1 review
ralph -S              # Standard: 5 dev + 2 reviews (default)
ralph -T              # Thorough: 10 dev + 5 reviews
ralph -L              # Long: 15 dev + 10 reviews
```

### Custom Iterations

```bash
ralph -D 3 -R 2       # 3 dev iterations, 2 review cycles
ralph -D 10 -R 0      # Skip review phase entirely
```

### Choose Agents

```bash
ralph -a claude -r codex    # Claude for dev, Codex for review
ralph -a opencode           # Use OpenCode for development
```

### Verbosity Control

```bash
ralph -q              # Quiet mode
ralph -f              # Full output (no truncation)
ralph -d              # Diagnose: show system info
```

### Recovery

```bash
ralph --resume                         # Resume from last checkpoint
ralph --dry-run                        # Validate setup without running
```

## Configuration

Ralph uses `~/.config/ralph-workflow.toml`:

```bash
ralph --init              # Smart init: creates config or PROMPT.md as needed
ralph --init bug-fix      # Create PROMPT.md from a specific Work Guide
ralph --list-work-guides  # Show all available Work Guides
ralph --extended-help     # Show comprehensive help
```

Configure reusable agent chains and bind drains to them:

```toml
[general]
developer_iters = 5
reviewer_reviews = 2

[agent_chains]
developer = ["claude", "codex", "opencode"]
reviewer = ["codex", "claude"]

[agent_drains]
planning = "developer"
development = "developer"
review = "reviewer"
fix = "reviewer"
commit = "reviewer"
analysis = "developer"

[agent_chain]
max_retries = 3
```

Environment variables override config:
- `RALPH_DEVELOPER_AGENT` - Developer agent
- `RALPH_REVIEWER_AGENT` - Reviewer agent
- `RALPH_DEVELOPER_ITERS` - Developer iterations
- `RALPH_REVIEWER_REVIEWS` - Review cycles
- `RALPH_VERBOSITY` - Output detail (0-4)

## Files Created by Ralph

```
.agent/
├── PLAN.md            # Current iteration plan (orchestrator-written)
├── ISSUES.md          # Review findings (orchestrator-written)
├── STATUS.md          # Current status
├── commit-message.txt # Generated commit message
├── checkpoint.json    # For --resume
├── start_commit       # Baseline for diffs
└── logs/              # Detailed per-phase logs
```

## Documentation

Full documentation is available on [Codeberg](https://codeberg.org/mistlight/RalphWithReviewer):

- **[Quick Reference](https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/docs/quick-reference.md)** - Cheat sheet for commands and flags
- **[Agent Compatibility](https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/docs/agent-compatibility.md)** - Supported AI agents and configuration
- **[Git Workflow](https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/docs/git-workflow.md)** - How Ralph handles commits and diffs
- **[Template Guide](https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/docs/template-guide.md)** - PROMPT.md Work Guides and agent prompt customization
- **[Architecture](https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/docs/architecture/README.md)** - Reducer/event-loop architecture and codebase tour

**Note:** When viewing on crates.io, these links point to the source repository on Codeberg.

## FAQ

**Can I use Ralph at work?**

Yes. Ralph is a local CLI tool. The AGPL license covers only the Ralph source code, not anything you create with it.

**Does AGPL apply to my generated code?**

No. The AGPL covers only Ralph itself, not your code or Ralph's output.

**What if Ralph gets interrupted?**

Use `ralph --resume` to continue from the last checkpoint.

## Cargo Features

| Feature | Default | Description |
|---------|---------|-------------|
| `monitoring` | Yes | Enable streaming metrics and debugging APIs |
| `test-utils` | No | Enable test utilities (TestLogger, TestPrinter, MemoryWorkspace) |
| `hardened-resume` | Yes | Enable execution history and file state capture for recovery |

To use `test-utils` for integration testing:

```toml
[dev-dependencies]
ralph-workflow = { version = "0.6", features = ["test-utils"] }
```

## Contributing

Contributions welcome!

1. Fork the repository
2. Create a feature branch
3. Run tests: `cargo test`
4. Run lints: `cargo clippy && cargo fmt --check`
5. Submit a pull request

## License

AGPL-3.0. See [LICENSE](https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/LICENSE).
