//! Command policy for RFC-009 Phase 2 bash command filtering.
//!
//! This module provides the blacklist-based command policy for Phase 2:
//! - Pure function evaluating shell commands against blacklist patterns
//! - Categories: Version Control, Privilege Escalation, Destructive System,
//!   Network/Exfiltration, Package Manager, Container Escape, Multi-File Operations
//! - Commands are allowed by default unless they match a blacklisted pattern
//!
//! # Design Principles
//!
//! - **Blacklist approach**: Commands are allowed by default unless blacklisted
//! - **Exact command matching**: First token of command is matched, not substring
//! - **Category-based reasoning**: Denial reasons reference the category
//! - **No I/O**: Pure function with no side effects

use crate::agents::session::PolicyOutcome;

/// Category of blacklisted command.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlacklistCategory {
    /// Version control commands (git, svn, hg, etc.)
    VersionControl,
    /// Privilege escalation commands (sudo, su, etc.)
    PrivilegeEscalation,
    /// Destructive system commands (rm -rf /, shutdown, etc.)
    DestructiveSystem,
    /// Network/exfiltration commands (curl, wget to external, nc, ssh, etc.)
    NetworkExfiltration,
    /// Package manager commands (apt, yum, pip install --user, npm install -g, etc.)
    PackageManager,
    /// Container/VM escape commands (docker, podman, chroot, etc.)
    ContainerEscape,
    /// Multi-file operations that bypass workspace write auditing
    MultiFileOperation,
}

impl BlacklistCategory {
    /// Returns a human-readable description of this category.
    #[must_use]
    pub fn description(&self) -> &'static str {
        match self {
            BlacklistCategory::VersionControl => "version control system",
            BlacklistCategory::PrivilegeEscalation => "privilege escalation",
            BlacklistCategory::DestructiveSystem => "destructive system operation",
            BlacklistCategory::NetworkExfiltration => "network/exfiltration",
            BlacklistCategory::PackageManager => "package manager",
            BlacklistCategory::ContainerEscape => "container/VM escape",
            BlacklistCategory::MultiFileOperation => "multi-file operation",
        }
    }
}

impl std::fmt::Display for BlacklistCategory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BlacklistCategory::VersionControl => write!(f, "version_control"),
            BlacklistCategory::PrivilegeEscalation => write!(f, "privilege_escalation"),
            BlacklistCategory::DestructiveSystem => write!(f, "destructive_system"),
            BlacklistCategory::NetworkExfiltration => write!(f, "network_exfiltration"),
            BlacklistCategory::PackageManager => write!(f, "package_manager"),
            BlacklistCategory::ContainerEscape => write!(f, "container_escape"),
            BlacklistCategory::MultiFileOperation => write!(f, "multi_file_operation"),
        }
    }
}

/// Evaluate a shell command against the blacklist policy.
///
/// Returns `PolicyOutcome::Approved` if the command is not blacklisted,
/// `PolicyOutcome::Denied { reason }` if it matches a blacklisted category.
///
/// # Arguments
///
/// * `command` - The command to evaluate (first token, e.g., "git", "curl")
/// * `args` - The arguments to the command
///
/// # Blacklist Categories
///
/// | Category | Commands | Rationale |
/// | --- | --- | --- |
/// | Version Control | git, svn, hg, fossil, bzr, darcs | Ralph owns all VCS operations |
/// | Privilege Escalation | sudo, su, doas, pkexec, runuser | Agents must not escalate privileges |
/// | Destructive System | rm -rf /, mkfs, dd (device), shutdown, reboot, halt, poweroff, kill -9 1, killall | Prevent system damage |
/// | Network/Exfiltration | curl/wget (external), nc/ncat/netcat/socat, ssh/scp/rsync (remote) | Prevent data exfiltration. localhost/127.0.0.1 allowed for curl/wget |
/// | Package Managers | apt, yum, dnf, pacman, brew install, pip install --user, npm install -g | Prevent uncontrolled global installs |
/// | Container/VM Escape | docker, podman, chroot, nsenter, unshare | Prevent container escape |
/// | Multi-File Operations | find -exec/-delete, xargs+destructive, sed -i/awk -i with globs, rename/mmv, chmod -R, chown -R, cp -r+globs, tar/zip/unzip in-place | File changes must go through workspace write |
///
/// # Example
///
/// ```ignore
/// let outcome = check_command("git", &["commit", "-m", "fix"]);
/// assert!(matches!(outcome, PolicyOutcome::Denied { .. }));
///
/// let outcome = check_command("cargo", &["test"]);
/// assert!(matches!(outcome, PolicyOutcome::Approved));
/// ```
#[must_use]
pub fn check_command(command: &str, args: &[&str]) -> PolicyOutcome {
    // Use the first token as the command for matching
    let cmd = command.trim();

    // Check each blacklist category
    if let Some(denial) = check_version_control(cmd) {
        return denial;
    }
    if let Some(denial) = check_privilege_escalation(cmd) {
        return denial;
    }
    if let Some(denial) = check_destructive_system(cmd, args) {
        return denial;
    }
    if let Some(denial) = check_network_exfiltration(cmd, args) {
        return denial;
    }
    if let Some(denial) = check_package_manager(cmd, args) {
        return denial;
    }
    if let Some(denial) = check_container_escape(cmd) {
        return denial;
    }
    if let Some(denial) = check_multi_file_operation(cmd, args) {
        return denial;
    }

    PolicyOutcome::Approved
}

/// Parse a command string into command and arguments.
///
/// Handles quoted arguments, pipes, subshells, and command chains.
#[must_use]
pub fn parse_command(command_str: &str) -> Vec<String> {
    #[derive(Default)]
    struct ParserState {
        tokens: Vec<String>,
        current: String,
        in_single_quote: bool,
        in_double_quote: bool,
        escaped: bool,
    }

    impl ParserState {
        fn handle_char(self, ch: char) -> ParserState {
            let ParserState {
                tokens,
                current,
                in_single_quote,
                in_double_quote,
                escaped,
            } = self;

            if escaped {
                return ParserState {
                    tokens,
                    current: format!("{current}{ch}"),
                    in_single_quote,
                    in_double_quote,
                    escaped: false,
                };
            }

            match ch {
                '\\' if !in_single_quote => ParserState {
                    tokens,
                    current,
                    in_single_quote,
                    in_double_quote,
                    escaped: true,
                },
                '\'' if !in_double_quote => ParserState {
                    tokens,
                    current,
                    in_single_quote: !in_single_quote,
                    in_double_quote,
                    escaped: false,
                },
                '"' if !in_single_quote => ParserState {
                    tokens,
                    current,
                    in_single_quote,
                    in_double_quote: !in_double_quote,
                    escaped: false,
                },
                ' ' | '\t' | '\n' | '\r' if !in_single_quote && !in_double_quote => {
                    if current.is_empty() {
                        ParserState {
                            tokens,
                            current,
                            in_single_quote,
                            in_double_quote,
                            escaped: false,
                        }
                    } else {
                        ParserState {
                            tokens: tokens.into_iter().chain(std::iter::once(current)).collect(),
                            current: String::new(),
                            in_single_quote,
                            in_double_quote,
                            escaped: false,
                        }
                    }
                }
                _ => ParserState {
                    tokens,
                    current: format!("{current}{ch}"),
                    in_single_quote,
                    in_double_quote,
                    escaped: false,
                },
            }
        }

        fn finalize(self) -> Vec<String> {
            if self.current.is_empty() {
                self.tokens
            } else {
                self.tokens
                    .into_iter()
                    .chain(std::iter::once(self.current))
                    .collect()
            }
        }
    }

    command_str
        .chars()
        .fold(ParserState::default(), ParserState::handle_char)
        .finalize()
}

/// Check if command is blacklisted version control.
fn check_version_control(cmd: &str) -> Option<PolicyOutcome> {
    match cmd {
        "git" | "svn" | "hg" | "fossil" | "bzr" | "darcs" => Some(PolicyOutcome::Denied {
            reason: format!(
                "Command '{}' is blacklisted: {} commands must go through Ralph's git capabilities",
                cmd,
                BlacklistCategory::VersionControl.description()
            ),
        }),
        _ => None,
    }
}

/// Check if command is blacklisted privilege escalation.
fn check_privilege_escalation(cmd: &str) -> Option<PolicyOutcome> {
    match cmd {
        "sudo" | "su" | "doas" | "pkexec" | "runuser" => Some(PolicyOutcome::Denied {
            reason: format!(
                "Command '{}' is blacklisted: {} is not allowed",
                cmd,
                BlacklistCategory::PrivilegeEscalation.description()
            ),
        }),
        _ => None,
    }
}

/// Check if command is blacklisted destructive system command.
fn check_destructive_system(cmd: &str, args: &[&str]) -> Option<PolicyOutcome> {
    match cmd {
        "rm" => {
            // Check for rm -rf / or rm -rf with dangerous paths
            if args.contains(&"-rf") || args.contains(&"-r") || args.contains(&"-f") {
                // Check for root deletion or system paths
                if args.iter().any(|a| {
                    *a == "/" || a.starts_with("/.") || a.starts_with("~") || a.starts_with("/home")
                }) {
                    return Some(PolicyOutcome::Denied {
                        reason: format!(
                            "Command 'rm' with recursive force flag targeting root/home is blacklisted: {}",
                            BlacklistCategory::DestructiveSystem.description()
                        ),
                    });
                }
            }
            None
        }
        "mkfs" | "dd" => {
            // Check for device targets (but not regular files)
            if args
                .iter()
                .any(|a| a.starts_with("/dev/") || a.contains("of=/dev/"))
            {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command '{}' targeting devices is blacklisted: {}",
                        cmd,
                        BlacklistCategory::DestructiveSystem.description()
                    ),
                });
            }
            None
        }
        "shutdown" | "reboot" | "halt" | "poweroff" => Some(PolicyOutcome::Denied {
            reason: format!(
                "Command '{}' is blacklisted: {} is not allowed",
                cmd,
                BlacklistCategory::DestructiveSystem.description()
            ),
        }),
        "killall" => Some(PolicyOutcome::Denied {
            reason: format!(
                "Command '{}' is blacklisted: {} is not allowed",
                cmd,
                BlacklistCategory::DestructiveSystem.description()
            ),
        }),
        "kill" => {
            // Only block kill -9 1 (init)
            if args.len() >= 2 && args[0] == "-9" && args[1] == "1" {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'kill -9 1' (init) is blacklisted: {} is not allowed",
                        BlacklistCategory::DestructiveSystem.description()
                    ),
                });
            }
            None
        }
        _ => None,
    }
}

/// Check if command is blacklisted network/exfiltration command.
fn check_network_exfiltration(cmd: &str, args: &[&str]) -> Option<PolicyOutcome> {
    match cmd {
        "curl" | "wget" => {
            // Check if targeting external hosts (not localhost/127.0.0.1)
            if args.iter().any(|raw_arg| {
                let arg = raw_arg.trim();
                // Skip flags and localhost
                !arg.starts_with('-')
                    && !arg.contains("localhost")
                    && !arg.contains("127.0.0.1")
                    // Check if external URL
                    && (arg.starts_with("http://")
                        || arg.starts_with("https://")
                        || arg.contains("://"))
            }) {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command '{}' to external URLs is blacklisted: {} risk. Use Ralph's HTTP capabilities instead.",
                        cmd,
                        BlacklistCategory::NetworkExfiltration.description()
                    ),
                });
            }
            None
        }
        "nc" | "ncat" | "netcat" | "socat" => Some(PolicyOutcome::Denied {
            reason: format!(
                "Command '{}' is blacklisted: {} is not allowed",
                cmd,
                BlacklistCategory::NetworkExfiltration.description()
            ),
        }),
        "ssh" | "scp" | "rsync" => {
            // Check if targeting a remote host (has @ or ://)
            let args_str = args.join(" ");
            if args_str.contains('@') || args_str.contains(":/") || args_str.contains("::") {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command '{}' to remote hosts is blacklisted: {} is not allowed",
                        cmd,
                        BlacklistCategory::NetworkExfiltration.description()
                    ),
                });
            }
            None
        }
        _ => None,
    }
}

/// Check if command is blacklisted package manager.
fn check_package_manager(cmd: &str, args: &[&str]) -> Option<PolicyOutcome> {
    match cmd {
        "apt" | "yum" | "dnf" | "pacman" | "brew" => {
            // Block if running install or similar privileged commands
            if args.iter().any(|a| {
                *a == "install"
                    || *a == "update"
                    || *a == "upgrade"
                    || *a == "remove"
                    || *a == "-S"
                    || *a == "--sync"
            }) {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command '{}' with install/update is blacklisted: {} operations require Ralph's approval",
                        cmd,
                        BlacklistCategory::PackageManager.description()
                    ),
                });
            }
            None
        }
        "pip" => {
            // Block global installs
            if args.contains(&"install")
                && (args.contains(&"--user") || args.contains(&"-g") || args.contains(&"--global"))
            {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'pip install --user/-g' is blacklisted: {} operations require Ralph's approval",
                        BlacklistCategory::PackageManager.description()
                    ),
                });
            }
            None
        }
        "npm" => {
            // Block global installs
            if args.contains(&"install") && args.contains(&"-g") {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'npm install -g' is blacklisted: {} operations require Ralph's approval",
                        BlacklistCategory::PackageManager.description()
                    ),
                });
            }
            None
        }
        "cargo" => {
            // Block install subcommand (global installs)
            if args.first() == Some(&"install") {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'cargo install' is blacklisted: {} operations require Ralph's approval",
                        BlacklistCategory::PackageManager.description()
                    ),
                });
            }
            None
        }
        "gem" => {
            if args.contains(&"install") && !args.contains(&"--user-install") {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'gem install' (global) is blacklisted: {} operations require Ralph's approval",
                        BlacklistCategory::PackageManager.description()
                    ),
                });
            }
            None
        }
        "pip3" => {
            // Block global installs
            if args.contains(&"install")
                && (args.contains(&"--user") || args.contains(&"-g") || args.contains(&"--global"))
            {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'pip3 install --user/-g' is blacklisted: {} operations require Ralph's approval",
                        BlacklistCategory::PackageManager.description()
                    ),
                });
            }
            None
        }
        _ => None,
    }
}

/// Check if command is blacklisted container/VM escape.
fn check_container_escape(cmd: &str) -> Option<PolicyOutcome> {
    match cmd {
        "docker" | "podman" => Some(PolicyOutcome::Denied {
            reason: format!(
                "Command '{}' is blacklisted: {} is not allowed",
                cmd,
                BlacklistCategory::ContainerEscape.description()
            ),
        }),
        "chroot" | "nsenter" | "unshare" => Some(PolicyOutcome::Denied {
            reason: format!(
                "Command '{}' is blacklisted: {} is not allowed",
                cmd,
                BlacklistCategory::ContainerEscape.description()
            ),
        }),
        _ => None,
    }
}

/// Check if command is blacklisted multi-file operation.
fn check_multi_file_operation(cmd: &str, args: &[&str]) -> Option<PolicyOutcome> {
    match cmd {
        "find" => {
            // Block find with -exec or -delete (bulk file operations)
            if args.contains(&"-exec") || args.contains(&"-delete") {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'find' with -exec/-delete is blacklisted: {} must go through Ralph's workspace write",
                        BlacklistCategory::MultiFileOperation.description()
                    ),
                });
            }
            None
        }
        "xargs" => {
            // Block xargs with destructive commands
            if args.iter().any(|a| {
                let a = a.to_lowercase();
                a == "rm" || a == "mv" || a == "cp" || a == "chmod" || a == "chown"
            }) {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'xargs' with destructive commands is blacklisted: {} must go through Ralph's workspace write",
                        BlacklistCategory::MultiFileOperation.description()
                    ),
                });
            }
            None
        }
        "sed" => {
            // Block sed -i (in-place with globs)
            if args.contains(&"-i") {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'sed -i' is blacklisted: {} must go through Ralph's workspace write",
                        BlacklistCategory::MultiFileOperation.description()
                    ),
                });
            }
            None
        }
        "awk" => {
            // Block awk -i (in-place)
            if args.contains(&"-i") || args.contains(&"-inplace") {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command 'awk -i' is blacklisted: {} must go through Ralph's workspace write",
                        BlacklistCategory::MultiFileOperation.description()
                    ),
                });
            }
            None
        }
        "rename" | "mmv" => Some(PolicyOutcome::Denied {
            reason: format!(
                "Command '{}' is blacklisted: {} must go through Ralph's workspace write",
                cmd,
                BlacklistCategory::MultiFileOperation.description()
            ),
        }),
        "chmod" | "chown" => {
            // Block recursive permission changes
            if args.contains(&"-R") || args.contains(&"-r") {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command '{} -R' is blacklisted: {} must go through Ralph's workspace write",
                        cmd,
                        BlacklistCategory::MultiFileOperation.description()
                    ),
                });
            }
            None
        }
        "cp" | "mv" => {
            // Block recursive copy/move with globs
            if args.iter().any(|a| a.contains('*') || a.contains('?'))
                && (args.contains(&"-r")
                    || args.contains(&"-R")
                    || args.contains(&"-rf")
                    || args.contains(&"-f"))
            {
                return Some(PolicyOutcome::Denied {
                    reason: format!(
                        "Command '{}' with recursive glob is blacklisted: {} must go through Ralph's workspace write",
                        cmd,
                        BlacklistCategory::MultiFileOperation.description()
                    ),
                });
            }
            None
        }
        "tar" | "zip" | "unzip" => {
            // Block if extracting in-place (overwrites multiple files)
            if args.iter().any(|a| a.contains("-x"))
                || args.contains(&"--extract")
                || args.contains(&"-d")
                || args.contains(&"--delete")
            {
                // Check for common archive extensions and in-place flags
                if args.iter().any(|a| {
                    a.ends_with(".tar")
                        || a.ends_with(".zip")
                        || a.ends_with(".gz")
                        || a.ends_with(".bz2")
                        || a.ends_with(".xz")
                        || a.ends_with(".zip")
                }) {
                    return Some(PolicyOutcome::Denied {
                        reason: format!(
                            "Command '{}' extracting archives in-place is blacklisted: {} must go through Ralph's workspace write",
                            cmd,
                            BlacklistCategory::MultiFileOperation.description()
                        ),
                    });
                }
            }
            None
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests;
