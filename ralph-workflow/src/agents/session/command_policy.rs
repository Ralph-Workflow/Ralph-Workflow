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
    let mut tokens = Vec::new();
    let mut current = String::new();
    let mut in_single_quote = false;
    let mut in_double_quote = false;
    let mut escaped = false;

    for ch in command_str.chars() {
        if escaped {
            current.push(ch);
            escaped = false;
            continue;
        }

        match ch {
            '\\' if !in_single_quote => {
                escaped = true;
            }
            '\'' if !in_double_quote => {
                in_single_quote = !in_single_quote;
            }
            '"' if !in_single_quote => {
                in_double_quote = !in_double_quote;
            }
            ' ' | '\t' | '\n' | '\r' if !in_single_quote && !in_double_quote => {
                if !current.is_empty() {
                    tokens.push(current.clone());
                    current.clear();
                }
            }
            _ => {
                current.push(ch);
            }
        }
    }

    if !current.is_empty() {
        tokens.push(current);
    }

    tokens
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
    let _args_str = args.join(" ");

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
            for arg in args {
                let arg = arg.trim();
                // Skip flags
                if arg.starts_with('-') {
                    continue;
                }
                // Allow localhost and 127.0.0.1
                if arg.contains("localhost") || arg.contains("127.0.0.1") {
                    continue;
                }
                // Check if it looks like a URL (has :// or starts with http)
                if arg.starts_with("http://") || arg.starts_with("https://") || arg.contains("://")
                {
                    // It's an external URL - block it
                    return Some(PolicyOutcome::Denied {
                        reason: format!(
                            "Command '{}' to external URLs is blacklisted: {} risk. Use Ralph's HTTP capabilities instead.",
                            cmd,
                            BlacklistCategory::NetworkExfiltration.description()
                        ),
                    });
                }
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
    let _args_str = args.join(" ");

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
mod tests {
    use super::*;

    // ===================================================================
    // Version Control tests
    // ===================================================================

    #[test]
    fn git_command_is_denied() {
        let outcome = check_command("git", &["commit", "-m", "fix"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "git should be denied: {:?}",
            outcome
        );
    }

    #[test]
    fn svn_command_is_denied() {
        let outcome = check_command("svn", &["update"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "svn should be denied"
        );
    }

    #[test]
    fn hg_command_is_denied() {
        let outcome = check_command("hg", &["status"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "hg should be denied"
        );
    }

    #[test]
    fn fossil_command_is_denied() {
        let outcome = check_command("fossil", &["commit"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "fossil should be denied"
        );
    }

    #[test]
    fn git_as_substring_not_blocked() {
        // "digital" contains "git" but should NOT be blocked
        let outcome = check_command("digital", &["commit"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "digital should NOT be blocked even though it contains 'git'"
        );
    }

    #[test]
    fn github_cli_is_allowed() {
        // "gh" is not a git command
        let outcome = check_command("gh", &["pr", "view"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "gh (GitHub CLI) should be allowed"
        );
    }

    // ===================================================================
    // Privilege Escalation tests
    // ===================================================================

    #[test]
    fn sudo_command_is_denied() {
        let outcome = check_command("sudo", &["apt", "install", "vim"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "sudo should be denied"
        );
    }

    #[test]
    fn su_command_is_denied() {
        let outcome = check_command("su", &["-", "root"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "su should be denied"
        );
    }

    #[test]
    fn doas_command_is_denied() {
        let outcome = check_command("doas", &["rm", "/etc/somefile"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "doas should be denied"
        );
    }

    #[test]
    fn pkexec_command_is_denied() {
        let outcome = check_command("pkexec", &["rm", "/tmp/file"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "pkexec should be denied"
        );
    }

    // ===================================================================
    // Destructive System tests
    // ===================================================================

    #[test]
    fn rm_rf_root_is_denied() {
        let outcome = check_command("rm", &["-rf", "/"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "rm -rf / should be denied"
        );
    }

    #[test]
    fn rm_rf_home_is_denied() {
        let outcome = check_command("rm", &["-rf", "/home/user"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "rm -rf /home should be denied"
        );
    }

    #[test]
    fn rm_without_flags_is_allowed() {
        let outcome = check_command("rm", &["/tmp/file.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "rm without -rf should be allowed"
        );
    }

    #[test]
    fn shutdown_command_is_denied() {
        let outcome = check_command("shutdown", &["-h", "now"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "shutdown should be denied"
        );
    }

    #[test]
    fn reboot_command_is_denied() {
        let outcome = check_command("reboot", &[]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "reboot should be denied"
        );
    }

    #[test]
    fn killall_is_denied() {
        let outcome = check_command("killall", &["sshd"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "killall should be denied"
        );
    }

    #[test]
    fn kill_9_1_is_denied() {
        let outcome = check_command("kill", &["-9", "1"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "kill -9 1 should be denied"
        );
    }

    #[test]
    fn kill_with_other_signal_is_allowed() {
        let outcome = check_command("kill", &["-15", "1234"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "kill -15 (SIGTERM) should be allowed"
        );
    }

    #[test]
    fn dd_to_device_is_denied() {
        let outcome = check_command("dd", &["if=/dev/zero", "of=/dev/sda"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "dd to device should be denied"
        );
    }

    #[test]
    fn dd_without_device_is_allowed() {
        let outcome = check_command("dd", &["if=/tmp/zero", "of=/tmp/test", "bs=1M", "count=1"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "dd without device should be allowed"
        );
    }

    // ===================================================================
    // Network/Exfiltration tests
    // ===================================================================

    #[test]
    fn curl_to_external_url_is_denied() {
        let outcome = check_command("curl", &["https://example.com/api"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "curl to external URL should be denied"
        );
    }

    #[test]
    fn curl_to_localhost_is_allowed() {
        let outcome = check_command("curl", &["http://localhost:8080/api"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "curl to localhost should be allowed"
        );
    }

    #[test]
    fn curl_to_127_0_0_1_is_allowed() {
        let outcome = check_command("curl", &["http://127.0.0.1:3000/health"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "curl to 127.0.0.1 should be allowed"
        );
    }

    #[test]
    fn wget_to_external_url_is_denied() {
        let outcome = check_command("wget", &["https://example.com/file.tar.gz"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "wget to external URL should be denied"
        );
    }

    #[test]
    fn wget_to_localhost_is_allowed() {
        let outcome = check_command("wget", &["http://localhost:8080/file.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "wget to localhost should be allowed"
        );
    }

    #[test]
    fn nc_is_denied() {
        let outcome = check_command("nc", &["-l", "8080"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "nc should be denied"
        );
    }

    #[test]
    fn ncat_is_denied() {
        let outcome = check_command("ncat", &["-l", "8080"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "ncat should be denied"
        );
    }

    #[test]
    fn ssh_to_remote_is_denied() {
        let outcome = check_command("ssh", &["user@hostname", "ls"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "ssh to remote should be denied"
        );
    }

    #[test]
    fn scp_to_remote_is_denied() {
        let outcome = check_command("scp", &["file.txt", "user@hostname:/tmp/"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "scp to remote should be denied"
        );
    }

    #[test]
    fn rsync_to_remote_is_denied() {
        let outcome = check_command("rsync", &["-avz", "data/", "user@hostname:/tmp/"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "rsync to remote should be denied"
        );
    }

    // ===================================================================
    // Package Manager tests
    // ===================================================================

    #[test]
    fn apt_install_is_denied() {
        let outcome = check_command("apt", &["install", "vim"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "apt install should be denied"
        );
    }

    #[test]
    fn yum_install_is_denied() {
        let outcome = check_command("yum", &["install", "vim"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "yum install should be denied"
        );
    }

    #[test]
    fn dnf_install_is_denied() {
        let outcome = check_command("dnf", &["install", "vim"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "dnf install should be denied"
        );
    }

    #[test]
    fn pacman_install_is_denied() {
        let outcome = check_command("pacman", &["-S", "vim"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "pacman install should be denied"
        );
    }

    #[test]
    fn brew_install_is_denied() {
        let outcome = check_command("brew", &["install", "vim"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "brew install should be denied"
        );
    }

    #[test]
    fn pip_install_user_is_denied() {
        let outcome = check_command("pip", &["install", "--user", "requests"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "pip install --user should be denied"
        );
    }

    #[test]
    fn pip_install_global_is_denied() {
        let outcome = check_command("pip", &["install", "-g", "requests"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "pip install -g should be denied"
        );
    }

    #[test]
    fn npm_install_global_is_denied() {
        let outcome = check_command("npm", &["install", "-g", "typescript"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "npm install -g should be denied"
        );
    }

    #[test]
    fn cargo_install_is_denied() {
        let outcome = check_command("cargo", &["install", "ripgrep"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "cargo install should be denied"
        );
    }

    #[test]
    fn apt_without_install_is_allowed() {
        let outcome = check_command("apt", &["-cache", "search", "vim"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "apt without install should be allowed"
        );
    }

    #[test]
    fn pip_install_local_is_allowed() {
        let outcome = check_command("pip", &["install", "requests"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "pip install (local) should be allowed"
        );
    }

    #[test]
    fn npm_install_local_is_allowed() {
        let outcome = check_command("npm", &["install", "lodash"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "npm install (local) should be allowed"
        );
    }

    // ===================================================================
    // Container Escape tests
    // ===================================================================

    #[test]
    fn docker_command_is_denied() {
        let outcome = check_command("docker", &["run", "-it", "ubuntu", "bash"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "docker should be denied"
        );
    }

    #[test]
    fn podman_command_is_denied() {
        let outcome = check_command("podman", &["run", "-it", "fedora", "bash"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "podman should be denied"
        );
    }

    #[test]
    fn chroot_command_is_denied() {
        let outcome = check_command("chroot", &["/path/to/root", "/bin/bash"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "chroot should be denied"
        );
    }

    #[test]
    fn nsenter_command_is_denied() {
        let outcome = check_command("nsenter", &["--target", "1234", "--mount", "bash"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "nsenter should be denied"
        );
    }

    #[test]
    fn unshare_command_is_denied() {
        let outcome = check_command("unshare", &["--mount", "bash"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "unshare should be denied"
        );
    }

    // ===================================================================
    // Multi-File Operation tests
    // ===================================================================

    #[test]
    fn find_exec_is_denied() {
        let outcome = check_command("find", &["/", "-name", "*.txt", "-exec", "rm", "{}"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "find -exec should be denied"
        );
    }

    #[test]
    fn find_delete_is_denied() {
        let outcome = check_command("find", &["/tmp", "-name", "*.tmp", "-delete"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "find -delete should be denied"
        );
    }

    #[test]
    fn xargs_with_rm_is_denied() {
        let outcome = check_command("xargs", &["rm", "-rf"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "xargs rm should be denied"
        );
    }

    #[test]
    fn sed_inplace_is_denied() {
        let outcome = check_command("sed", &["-i", "s/foo/bar/g", "*.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "sed -i should be denied"
        );
    }

    #[test]
    fn awk_inplace_is_denied() {
        let outcome = check_command("awk", &["-i", "{print $1}", "file.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "awk -i should be denied"
        );
    }

    #[test]
    fn rename_command_is_denied() {
        let outcome = check_command("rename", &[".txt", ".md", "*.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "rename should be denied"
        );
    }

    #[test]
    fn chmod_recursive_is_denied() {
        let outcome = check_command("chmod", &["-R", "755", "/path"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "chmod -R should be denied"
        );
    }

    #[test]
    fn chown_recursive_is_denied() {
        let outcome = check_command("chown", &["-R", "user:group", "/path"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "chown -R should be denied"
        );
    }

    #[test]
    fn cp_recursive_with_glob_is_denied() {
        let outcome = check_command("cp", &["-r", "*.txt", "/destination/"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "cp -r with glob should be denied"
        );
    }

    #[test]
    fn tar_extract_is_denied() {
        let outcome = check_command("tar", &["-xvf", "archive.tar.gz"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "tar extract should be denied"
        );
    }

    #[test]
    fn tar_create_is_allowed() {
        let outcome = check_command("tar", &["-cvf", "archive.tar.gz", "file1.txt", "file2.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "tar create should be allowed"
        );
    }

    // ===================================================================
    // Allowed commands tests
    // ===================================================================

    #[test]
    fn cargo_test_is_allowed() {
        let outcome = check_command("cargo", &["test"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "cargo test should be allowed"
        );
    }

    #[test]
    fn cargo_build_is_allowed() {
        let outcome = check_command("cargo", &["build"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "cargo build should be allowed"
        );
    }

    #[test]
    fn rustfmt_is_allowed() {
        let outcome = check_command("rustfmt", &["src/main.rs"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "rustfmt should be allowed"
        );
    }

    #[test]
    fn npm_test_is_allowed() {
        let outcome = check_command("npm", &["test"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "npm test should be allowed"
        );
    }

    #[test]
    fn make_is_allowed() {
        let outcome = check_command("make", &["build"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "make should be allowed"
        );
    }

    #[test]
    fn ls_is_allowed() {
        let outcome = check_command("ls", &["-la"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "ls should be allowed"
        );
    }

    #[test]
    fn cat_is_allowed() {
        let outcome = check_command("cat", &["file.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "cat should be allowed"
        );
    }

    #[test]
    fn grep_is_allowed() {
        let outcome = check_command("grep", &["-r", "pattern", "."]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "grep should be allowed"
        );
    }

    // ===================================================================
    // Parse command tests
    // ===================================================================

    #[test]
    fn parse_simple_command() {
        let tokens = parse_command("git status");
        assert_eq!(tokens, vec!["git", "status"]);
    }

    #[test]
    fn parse_command_with_flags() {
        let tokens = parse_command("cargo test --lib");
        assert_eq!(tokens, vec!["cargo", "test", "--lib"]);
    }

    #[test]
    fn parse_quoted_args() {
        let tokens = parse_command("git commit -m \"fix: bug\"");
        assert_eq!(tokens, vec!["git", "commit", "-m", "fix: bug"]);
    }

    #[test]
    fn parse_single_quoted_args() {
        let tokens = parse_command("echo 'hello world'");
        assert_eq!(tokens, vec!["echo", "hello world"]);
    }

    #[test]
    fn parse_command_with_pipe() {
        let tokens = parse_command("ls | grep foo");
        assert_eq!(tokens, vec!["ls", "|", "grep", "foo"]);
    }

    // ===================================================================
    // Denial reason tests
    // ===================================================================

    #[test]
    fn denial_includes_category() {
        let outcome = check_command("git", &["status"]);
        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(
                reason.contains("version_control") || reason.contains("version control"),
                "Denial reason should mention category"
            );
        } else {
            panic!("Expected denial, got {:?}", outcome);
        }
    }

    #[test]
    fn denial_includes_command_name() {
        let outcome = check_command("sudo", &["ls"]);
        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(
                reason.contains("sudo"),
                "Denial reason should mention the command"
            );
        } else {
            panic!("Expected denial, got {:?}", outcome);
        }
    }
}
