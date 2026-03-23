const EXTENDED_HELP_TEXT: &str = r#"RALPH EXTENDED HELP
===============================================================================

Ralph is a PROMPT-driven multi-agent orchestrator for git repos. It runs a
developer agent for code implementation, then a reviewer agent for quality
assurance and fixes (default), automatically staging and committing the final result.

===============================================================================
GETTING STARTED
===============================================================================

  1. Initialize config:
       ralph --init                      # Smart init (infers what you need)

  2. Create a PROMPT.md from a Work Guide:
       ralph --init feature-spec         # Or: bug-fix, refactor, quick, etc.

  3. Edit PROMPT.md with your task details

  4. Run Ralph:
       ralph "fix: my bug description"   # Commit message for the final commit

===============================================================================
WORK GUIDES VS AGENT PROMPTS
===============================================================================

  Ralph has two types of templates - understanding the difference is key:

  1. WORK GUIDES (for PROMPT.md - YOUR task descriptions)
     -------------------------------------------------
     These are templates for describing YOUR work to the AI.
     You fill them in with your specific task requirements.

     Examples: quick, bug-fix, feature-spec, refactor, test, docs

     Commands:
       ralph --init <work-guide>      Create PROMPT.md from a Work Guide
       ralph --list-work-guides       Show all available Work Guides

  2. AGENT PROMPTS (backend AI behavior configuration)
     -------------------------------------------------
     These configure HOW the AI agents behave (internal system prompts).
     You probably don't need to touch these unless customizing agent behavior.

     Commands:
       ralph --init-system-prompts    Create default Agent Prompts
       ralph --list                   Show Agent Prompt templates
       ralph --show <name>            Show a specific Agent Prompt

===============================================================================
PRESET MODES
===============================================================================

  Pick how thorough the AI should be:

    -Q  Quick:      1 dev iteration  + 1 review   (typos, small fixes)
    -U  Rapid:      2 dev iterations + 1 review   (minor changes)
    -S  Standard:   5 dev iterations + 2 reviews  (default for most tasks)
    -T  Thorough:  10 dev iterations + 5 reviews  (complex features)
    -L  Long:      15 dev iterations + 10 reviews (most thorough)

  Custom iterations:
    ralph -D 3 -R 2 "feat: feature"   # 3 dev iterations, 2 review cycles
    ralph -D 10 -R 0 "feat: no review"  # Skip review phase entirely

===============================================================================
COMMON OPTIONS
===============================================================================

  Iterations:
    -D N, --developer-iters N   Set developer iterations
    -R N, --reviewer-reviews N  Set review cycles (0 = skip review)

  Agents:
    -a AGENT, --developer-agent AGENT   Pick developer agent
    -r AGENT, --reviewer-agent AGENT    Pick reviewer agent

  Verbosity:
    -q, --quiet          Quiet mode (minimal output)
    -f, --full           Full output (no truncation)
    -v N, --verbosity N  Set verbosity (0-4)

  Other:
    -d, --diagnose       Show system info and agent status

===============================================================================
ADVANCED OPTIONS
===============================================================================

  These options are hidden from the main --help to reduce clutter.

  Initialization:
    --force-overwrite            Overwrite PROMPT.md without prompting
    -i, --interactive            Prompt for PROMPT.md if missing

  Git Control:
    --with-rebase                Enable automatic rebase to main branch (disabled by default)
    --rebase-only                Only rebase, then exit (no pipeline)
    --git-user-name <name>       Override git user name for commits
    --git-user-email <email>     Override git user email for commits

  Recovery:
    --resume                     Resume from last checkpoint
    --dry-run                    Validate setup without running agents

  Agent Prompt Management:
    --init-system-prompts        Create default Agent Prompt templates
    --list                       List all Agent Prompt templates
    --show <name>                Show Agent Prompt content
    --validate                   Validate Agent Prompt templates
    --variables <name>           Extract variables from template
    --render <name>              Test render a template

  Debugging:
    --show-streaming-metrics     Show JSON streaming quality metrics
    -c PATH, --config PATH       Use specific config file

===============================================================================
SHELL COMPLETION
===============================================================================

  Enable tab-completion for faster command entry:

    Bash:
      ralph --generate-completion=bash > ~/.local/share/bash-completion/completions/ralph

    Zsh:
      ralph --generate-completion=zsh > ~/.zsh/completion/_ralph

    Fish:
      ralph --generate-completion=fish > ~/.config/fish/completions/ralph.fish

  Then restart your shell or source the file.

===============================================================================
TROUBLESHOOTING
===============================================================================

  Common issues:

    "PROMPT.md not found"
      -> Run: ralph --init <work-guide>  (e.g., ralph --init bug-fix)

    "No agents available"
      -> Run: ralph -d  (diagnose) to check agent status
      -> Ensure at least one agent is installed (claude, codex, opencode)

    "Config file not found"
      -> Run: ralph --init  to create ~/.config/ralph-workflow.toml

    Resume after interruption:
      -> Run: ralph --resume  to continue from last checkpoint

    Validate setup without running:
      -> Run: ralph --dry-run

===============================================================================
EXAMPLES
===============================================================================

    ralph "fix: typo"                 Run with default settings
    ralph -Q "fix: small bug"         Quick mode for tiny fixes
    ralph -U "feat: add button"       Rapid mode for minor features
    ralph -a claude "fix: bug"        Use specific agent
    ralph --list-work-guides          See all Work Guides
    ralph --init bug-fix              Create PROMPT.md from a Work Guide
    ralph --init bug-fix --force-overwrite  Overwrite existing PROMPT.md

===============================================================================
"#;

/// Handle the `--extended-help` / `--man` flag.
///
/// Displays comprehensive help including shell completion, all presets,
/// troubleshooting information, and the difference between Work Guides and Agent Prompts.
pub fn handle_extended_help() {
    let _ = writeln!(std::io::stdout(), "{EXTENDED_HELP_TEXT}");
}
