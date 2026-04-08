//! Pure domain functions for main subcommand policy.
//!
//! This module contains the policy for parsing command-line arguments and
//! determining which subcommand to execute. No I/O, no process spawning.

/// Represents a subcommand to execute.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Subcommand {
    /// Run all verification checks.
    Verify { include_gui: bool },
    /// Run custom dylint lints.
    Dylint { verbose: bool },
    /// Emit LSP diagnostics for forbidden allow/expect.
    LspForbidAllowExpect,
    /// Generate dylint reports.
    DylintReport,
    /// Run coverage.
    Coverage,
    /// Show help.
    Help { subcommand: Option<&'static str> },
    /// Unknown subcommand - show usage.
    Unknown,
}

/// Parse command-line arguments and determine which subcommand to run.
///
/// This is a pure function - it only examines the input arguments.
pub fn parse_subcommand(args: &[String]) -> Subcommand {
    let subcommand = args.first().map(|s| s.as_str());

    match subcommand {
        Some("verify") => {
            if args.contains(&"--help".to_string()) || args.contains(&"-h".to_string()) {
                return Subcommand::Help {
                    subcommand: Some("verify"),
                };
            }
            let include_gui = args.contains(&"--gui".to_string());
            Subcommand::Verify { include_gui }
        }
        Some("dylint") => {
            if args.contains(&"--help".to_string()) || args.contains(&"-h".to_string()) {
                return Subcommand::Help {
                    subcommand: Some("dylint"),
                };
            }
            let verbose =
                args.contains(&"--verbose".to_string()) || args.contains(&"-v".to_string());
            Subcommand::Dylint { verbose }
        }
        Some("lsp-forbidden-allow-expect") => {
            if args.contains(&"--help".to_string()) || args.contains(&"-h".to_string()) {
                return Subcommand::Help {
                    subcommand: Some("lsp-forbidden-allow-expect"),
                };
            }
            Subcommand::LspForbidAllowExpect
        }
        Some("dylint-report") => {
            if args.contains(&"--help".to_string()) || args.contains(&"-h".to_string()) {
                return Subcommand::Help {
                    subcommand: Some("dylint-report"),
                };
            }
            Subcommand::DylintReport
        }
        Some("coverage") => {
            if args.contains(&"--help".to_string()) || args.contains(&"-h".to_string()) {
                return Subcommand::Help {
                    subcommand: Some("coverage"),
                };
            }
            Subcommand::Coverage
        }
        _ => Subcommand::Unknown,
    }
}
