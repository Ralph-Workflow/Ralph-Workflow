/// Commit message plumbing flags.
#[derive(Parser, Debug, Default)]
pub struct CommitPlumbingFlags {
    /// Generate commit message only (writes to .agent/commit-message.txt)
    #[arg(
        long,
        help = "Run only the commit message generation phase, then exit",
        hide = true
    )]
    pub generate_commit_msg: bool,

    /// Apply commit using existing .agent/commit-message.txt
    #[arg(
        long,
        help = "Stage all changes and commit using .agent/commit-message.txt",
        hide = true
    )]
    pub apply_commit: bool,

    /// Generate commit message and immediately apply it (combines --generate-commit-msg + --apply-commit)
    #[arg(
        long,
        help = "Generate a commit message and immediately create the commit",
        hide = true
    )]
    pub generate_commit: bool,

    /// Run as MCP stdio-to-socket proxy (used by Claude Code MCP config)
    #[arg(long, help = "Run as MCP stdio-to-socket proxy", hide = true)]
    pub mcp_proxy: bool,
}

/// Commit display plumbing flags.
///
/// This groups flags for displaying commit-related information.
#[derive(Parser, Debug, Default)]
pub struct CommitDisplayFlags {
    /// Show the generated commit message and exit
    #[arg(long, help = "Read and display .agent/commit-message.txt", hide = true)]
    pub show_commit_msg: bool,

    /// Reset the starting commit reference to merge-base with main branch
    #[arg(
        long,
        help = "Reset .agent/start_commit to merge-base with main branch (for incremental diff generation)",
        hide = true
    )]
    pub reset_start_commit: bool,

    /// Show the current baseline state and exit
    /// Handler: `cli/handlers/baseline.rs::handle_show_baseline()`
    #[arg(long, help = "Show current review baseline and start commit state")]
    pub show_baseline: bool,
}
