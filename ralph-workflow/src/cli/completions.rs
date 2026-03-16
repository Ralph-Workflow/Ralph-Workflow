//! Shell completion generation handlers.
//!
//! This module handles the `--generate-completion` flag for generating
//! shell completion scripts for bash, zsh, fish, elvish, and powershell.

use crate::cli::args::Shell;
use clap::CommandFactory;

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
#[expect(
    clippy::print_stderr,
    reason = "CLI completion installation instructions"
)]
pub fn handle_generate_completion(shell: Shell) -> bool {
    let mut stdout = std::io::stdout();
    let shell_name = shell.name();

    // Get the command from Args
    let mut command = crate::cli::Args::command();

    // Generate the completion script using clap_complete
    let shell_type = match shell {
        Shell::Bash => clap_complete::Shell::Bash,
        Shell::Zsh => clap_complete::Shell::Zsh,
        Shell::Fish => clap_complete::Shell::Fish,
        Shell::Elvish => clap_complete::Shell::Elvish,
        Shell::Pwsh => clap_complete::Shell::PowerShell,
    };

    clap_complete::generate(shell_type, &mut command, "ralph", &mut stdout);

    // Print installation instructions
    eprintln!();
    eprintln!("=== Shell completion installation for {shell_name} ===");
    eprintln!();
    eprintln!("To enable completions, add the following to your shell config:");
    eprintln!();

    match shell {
        Shell::Bash => {
            eprintln!("  # Add to ~/.bashrc or ~/.bash_profile:");
            eprintln!("  source <(ralph --generate-completion=bash)");
            eprintln!();
            eprintln!("  # Or save to a file:");
            eprintln!("  ralph --generate-completion=bash > ~/.local/share/bash-completion/completions/ralph");
        }
        Shell::Zsh => {
            eprintln!("  # Add to ~/.zshrc:");
            eprintln!("  source <(ralph --generate-completion=zsh)");
            eprintln!();
            eprintln!("  # Or save to a file:");
            eprintln!("  ralph --generate-completion=zsh > ~/.zsh/completion/_ralph");
            eprintln!("  # Then add to ~/.zshrc:");
            eprintln!("  fpath=(~/.zsh/completion $fpath)");
            eprintln!("  autoload -U compinit && compinit");
        }
        Shell::Fish => {
            eprintln!("  # Add to ~/.config/fish/completions/ralph.fish:");
            eprintln!("  ralph --generate-completion=fish > ~/.config/fish/completions/ralph.fish");
        }
        Shell::Elvish => {
            eprintln!("  # Add to ~/.elvish/rc.elv:");
            eprintln!("  ralph --generate-completion=elvish > ~/.config/elvish/lib/ralph.elv");
            eprintln!("  # Then add to ~/.elvish/rc.elv:");
            eprintln!("  put ~/.config/elvish/lib/ralph.elv | slurp");
        }
        Shell::Pwsh => {
            eprintln!("  # Add to your PowerShell profile ($PROFILE):");
            eprintln!("  ralph --generate-completion=pwsh > ralph-completion.ps1");
            eprintln!("  # Then add to $PROFILE:");
            eprintln!("  . ralph-completion.ps1");
        }
    }

    eprintln!();
    eprintln!("Restart your shell or source your config file to apply changes.");

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
