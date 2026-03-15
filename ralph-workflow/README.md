# Ralph Workflow

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/LICENSE)
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

1. **Development Phase**: Runs for a configured number of iterations
   - **Outer loop**: Runs N iterations (configured by `-D` or `developer_iters`)
   - **Inner loop**: Plan → Develop → Analyze → Developer refines until satisfied
   - Auto-commits after each iteration

2. **Review Phase**: Runs for a configured number of cycles
   - **Outer loop**: Runs N cycles (configured by `-R` or `reviewer_reviews`)
   - **Inner loop**: Analyzer → Fixer loops until issues are resolved
   - Auto-commits after each cycle

3. **Commit Phase**: Generates a meaningful commit message via AI

Ralph automatically cleans AI agent context between phases to prevent context pollution. Each phase operates with fresh context—the reviewer sees only the diff and current code state, not the development history.

All orchestration is handled internally by Ralph, ensuring deterministic, reliable operation.

## Quick Start

### 1. Install

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow

# Install from source
cargo install --path ralph-workflow --locked

# Or build + install via Makefile
make install-local
```

Alternatively, you can install from crates.io:

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

**Note:** Work Guides should define the end goal of your task.

## Writing Effective Specifications

Your `PROMPT.md` should be detailed and product-focused. Describe WHAT to build, not HOW to build it.

```markdown
# Feature: Social Login

## Goal
Users can sign in with their GitHub or Google account instead of creating a new password.

## User Stories
- As a new user, I want to sign up using my GitHub account so I don't have to remember another password
- As a returning user, I want to log in with Google so I can access my account quickly
- As an existing user with an API key, I want my integrations to keep working without changes

## Acceptance Criteria
- [ ] "Sign in with GitHub" button on login page
- [ ] "Sign in with Google" button on login page
- [ ] First-time social login creates account and links provider
- [ ] Returning social login recognizes existing linked account
- [ ] Existing API key auth continues to work unchanged
- [ ] User profile shows which providers are linked
- [ ] Error messages are clear when provider is unavailable

## Edge Cases
- User tries to link a provider already linked to another account
- Provider returns an error or times out
- User's email from provider matches existing unlinked account

## Constraints
- No breaking changes to existing API authentication
- All existing tests must pass
- Follow our existing error handling patterns
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
planner   = ["claude", "codex"]
developer = ["opencode"]                         # default opencode agent
reviewer  = ["opencode/openai/gpt-5.2"]          # specify model for opencode

[agent_drains]
planning    = "planner"   # use good model for planning
development = "developer"
review      = "reviewer"
fix         = "developer"
commit      = "reviewer"
analysis    = "reviewer"  # runs after dev/fixer to review against plan
```

## Files Created by Ralph

```
.agent/
├── commit-message.txt # Generated commit message
├── checkpoint.json    # For --resume
├── start_commit       # Baseline for diffs
└── logs/              # Detailed per-phase logs
```

Internal orchestration files are managed by Ralph and should not be edited manually.

## Documentation

Full documentation is available on [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow):

- **[Quick Reference](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/quick-reference.md)** - Cheat sheet for commands and flags
- **[Agent Compatibility](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/agent-compatibility.md)** - Supported AI agents and configuration
- **[Git Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/git-workflow.md)** - How Ralph handles commits and diffs
- **[Template Guide](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/template-guide.md)** - PROMPT.md Work Guides and agent prompt customization
- **[Architecture](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/architecture/README.md)** - Reducer/event-loop architecture and codebase tour

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

AGPL-3.0. See [LICENSE](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/LICENSE).
