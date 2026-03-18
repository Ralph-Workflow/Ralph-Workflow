//! Shell completion generation handlers.
//!
//! This module handles the `--generate-completion` flag for generating
//! shell completion scripts for bash, zsh, fish, elvish, and powershell.

use crate::cli::args::Shell;
use clap::CommandFactory;
use std::io::Write;

/// Handle the `--generate-completion` flag.
///
/// Generates a shell completion script for the specified shell and writes it to stdout.
///
/// # Arguments
///
/// * `shell` - The shell type to generate completions for
///
/// # Returns
///
/// Returns `true` if the flag was handled (program should exit after).
#[must_use]
pub fn handle_generate_completion(shell: Shell) -> bool {
    let shell_name = shell.name();

    // Generate completion to stdout using a scope for the mutable references
    let shell_type = match shell {
        Shell::Bash => clap_complete::Shell::Bash,
        Shell::Zsh => clap_complete::Shell::Zsh,
        Shell::Fish => clap_complete::Shell::Fish,
        Shell::Elvish => clap_complete::Shell::Elvish,
        Shell::Pwsh => clap_complete::Shell::PowerShell,
    };

    clap_complete::generate(
        shell_type,
        &mut crate::cli::Args::command(),
        "ralph",
        &mut std::io::stdout(),
    );

    // Print installation instructions
    let _ = writeln!(std::io::stderr());
    let _ = writeln!(
        std::io::stderr(),
        "=== Shell completion installation for {shell_name} ==="
    );
    let _ = writeln!(std::io::stderr());
    let _ = writeln!(
        std::io::stderr(),
        "To enable completions, add the following to your shell config:"
    );
    let _ = writeln!(std::io::stderr());

    match shell {
        Shell::Bash => {
            let _ = writeln!(
                std::io::stderr(),
                "  # Add to ~/.bashrc or ~/.bash_profile:"
            );
            let _ = writeln!(
                std::io::stderr(),
                "  source <(ralph --generate-completion=bash)"
            );
            let _ = writeln!(std::io::stderr());
            let _ = writeln!(std::io::stderr(), "  # Or save to a file:");
            let _ = writeln!(std::io::stderr(), "  ralph --generate-completion=bash > ~/.local/share/bash-completion/completions/ralph");
        }
        Shell::Zsh => {
            let _ = writeln!(std::io::stderr(), "  # Add to ~/.zshrc:");
            let _ = writeln!(
                std::io::stderr(),
                "  source <(ralph --generate-completion=zsh)"
            );
            let _ = writeln!(std::io::stderr());
            let _ = writeln!(std::io::stderr(), "  # Or save to a file:");
            let _ = writeln!(
                std::io::stderr(),
                "  ralph --generate-completion=zsh > ~/.zsh/completion/_ralph"
            );
            let _ = writeln!(std::io::stderr(), "  # Then add to ~/.zshrc:");
            let _ = writeln!(std::io::stderr(), "  fpath=(~/.zsh/completion $fpath)");
            let _ = writeln!(std::io::stderr(), "  autoload -U compinit && compinit");
        }
        Shell::Fish => {
            let _ = writeln!(
                std::io::stderr(),
                "  # Add to ~/.config/fish/completions/ralph.fish:"
            );
            let _ = writeln!(
                std::io::stderr(),
                "  ralph --generate-completion=fish > ~/.config/fish/completions/ralph.fish"
            );
        }
        Shell::Elvish => {
            let _ = writeln!(std::io::stderr(), "  # Add to ~/.elvish/rc.elv:");
            let _ = writeln!(
                std::io::stderr(),
                "  ralph --generate-completion=elvish > ~/.config/elvish/lib/ralph.elv"
            );
            let _ = writeln!(std::io::stderr(), "  # Then add to ~/.elvish/rc.elv:");
            let _ = writeln!(
                std::io::stderr(),
                "  put ~/.config/elvish/lib/ralph.elv | slurp"
            );
        }
        Shell::Pwsh => {
            let _ = writeln!(
                std::io::stderr(),
                "  # Add to your PowerShell profile ($PROFILE):"
            );
            let _ = writeln!(
                std::io::stderr(),
                "  ralph --generate-completion=pwsh > ralph-completion.ps1"
            );
            let _ = writeln!(std::io::stderr(), "  # Then add to $PROFILE:");
            let _ = writeln!(std::io::stderr(), "  . ralph-completion.ps1");
        }
    }

    let _ = writeln!(std::io::stderr());
    let _ = writeln!(
        std::io::stderr(),
        "Restart your shell or source your config file to apply changes."
    );

    true
}

impl Shell {
    /// Returns the name of the shell as a string.
    pub const fn name(self) -> &'static str {
        match self {
            Self::Bash => "bash",
            Self::Zsh => "zsh",
            Self::Fish => "fish",
            Self::Elvish => "elvish",
            Self::Pwsh => "powershell",
        }
    }
}
