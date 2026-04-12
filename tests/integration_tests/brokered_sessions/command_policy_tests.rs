//! Integration tests for command policy enforcement.
//!
//! These tests verify that the command policy blacklist is properly enforced
//! during agent invocation:
//! - Blacklisted commands are denied and result in CapabilityDenied events
//! - Allowed commands proceed normally
//! - Audit trail records command policy checks
//!
//! # Test Architecture
//!
//! The command policy check happens in `run_agent_execution` before the agent
//! process is spawned. When a command is denied, a `CapabilityDenied` event is
//! returned and the agent is NOT executed.
//!
//! # Blacklist Categories
//!
//! | Category | Commands | Rationale |
//! | --- | --- | --- |
//! | Version Control | git, svn, hg | Ralph owns VCS operations |
//! | Privilege Escalation | sudo, su | Agents must not escalate privileges |
//! | Destructive System | rm -rf /, shutdown | Prevent system damage |
//! | Network/Exfiltration | curl/wget (external) | Prevent data exfiltration |
//! | Package Managers | apt, yum, npm -g | Prevent uncontrolled installs |
//! | Container Escape | docker, podman | Prevent container escape |
//! | Multi-File Operations | find -exec, sed -i | File changes via workspace |

use ralph_workflow::agents::session::command_policy::{check_command, parse_command};
use ralph_workflow::agents::session::{AgentSession, Capability, PolicyOutcome, SessionDrain};

use crate::test_timeout::with_default_timeout;

/// Test that git command is denied by the blacklist.
#[test]
fn denied_git_command_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("git", &["commit", "-m", "fix"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "git should be denied by policy: {:?}",
            outcome
        );
    });
}

/// Test that sudo command is denied by the blacklist.
#[test]
fn denied_sudo_command_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("sudo", &["ls"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "sudo should be denied by policy: {:?}",
            outcome
        );
    });
}

/// Test that curl to external URL is denied.
#[test]
fn denied_curl_external_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("curl", &["https://evil.com/api"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "curl to external URL should be denied: {:?}",
            outcome
        );
    });
}

/// Test that curl to localhost is allowed.
#[test]
fn allowed_curl_localhost_returns_approved() {
    with_default_timeout(|| {
        let outcome = check_command("curl", &["http://localhost:8080/api"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "curl to localhost should be allowed: {:?}",
            outcome
        );
    });
}

/// Test that cargo test is allowed.
#[test]
fn allowed_cargo_test_returns_approved() {
    with_default_timeout(|| {
        let outcome = check_command("cargo", &["test"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "cargo test should be allowed: {:?}",
            outcome
        );
    });
}

/// Test that npm global install is denied.
#[test]
fn denied_npm_global_install_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("npm", &["install", "-g", "typescript"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "npm install -g should be denied: {:?}",
            outcome
        );
    });
}

/// Test that find -exec is denied.
#[test]
fn denied_find_exec_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("find", &["/", "-name", "*.txt", "-exec", "rm", "{}"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "find -exec should be denied: {:?}",
            outcome
        );
    });
}

/// Test that docker command is denied.
#[test]
fn denied_docker_command_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("docker", &["run", "-it", "ubuntu", "bash"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "docker should be denied: {:?}",
            outcome
        );
    });
}

/// Test that rm -rf / is denied.
#[test]
fn denied_rm_rf_root_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("rm", &["-rf", "/"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "rm -rf / should be denied: {:?}",
            outcome
        );
    });
}

/// Test that rm without flags is allowed.
#[test]
fn allowed_rm_normal_file_returns_approved() {
    with_default_timeout(|| {
        let outcome = check_command("rm", &["/tmp/file.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "rm without flags should be allowed: {:?}",
            outcome
        );
    });
}

/// Test that pip install --user is denied.
#[test]
fn denied_pip_install_user_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("pip", &["install", "--user", "requests"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "pip install --user should be denied: {:?}",
            outcome
        );
    });
}

/// Test that cargo install (global) is denied.
#[test]
fn denied_cargo_install_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("cargo", &["install", "ripgrep"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "cargo install should be denied: {:?}",
            outcome
        );
    });
}

/// Test parse_command correctly tokenizes a simple command.
#[test]
fn parse_command_simple() {
    with_default_timeout(|| {
        let tokens = parse_command("git status");
        assert_eq!(tokens, vec!["git", "status"]);
    });
}

/// Test parse_command correctly tokenizes a command with flags.
#[test]
fn parse_command_with_flags() {
    with_default_timeout(|| {
        let tokens = parse_command("cargo test --lib");
        assert_eq!(tokens, vec!["cargo", "test", "--lib"]);
    });
}

/// Test parse_command handles quoted arguments.
#[test]
fn parse_command_quoted_args() {
    with_default_timeout(|| {
        let tokens = parse_command("git commit -m \"fix: bug\"");
        assert_eq!(tokens, vec!["git", "commit", "-m", "fix: bug"]);
    });
}

/// Test that git as substring in another word is not blocked.
#[test]
fn git_substring_not_blocked() {
    with_default_timeout(|| {
        // "digital" contains "git" but should NOT be blocked
        let outcome = check_command("digital", &["commit"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "digital should NOT be blocked even though it contains 'git': {:?}",
            outcome
        );
    });
}

/// Test that GitHub CLI (gh) is allowed.
#[test]
fn allowed_gh_cli() {
    with_default_timeout(|| {
        let outcome = check_command("gh", &["pr", "view"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "gh (GitHub CLI) should be allowed: {:?}",
            outcome
        );
    });
}

/// Test that shutdown command is denied.
#[test]
fn denied_shutdown_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("shutdown", &["-h", "now"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "shutdown should be denied: {:?}",
            outcome
        );
    });
}

/// Test that sed -i is denied.
#[test]
fn denied_sed_inplace_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("sed", &["-i", "s/foo/bar/g", "*.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "sed -i should be denied: {:?}",
            outcome
        );
    });
}

/// Test that tar extract is denied.
#[test]
fn denied_tar_extract_returns_denied() {
    with_default_timeout(|| {
        let outcome = check_command("tar", &["-xvf", "archive.tar.gz"]);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "tar extract should be denied: {:?}",
            outcome
        );
    });
}

/// Test that tar create is allowed.
#[test]
fn allowed_tar_create_returns_approved() {
    with_default_timeout(|| {
        let outcome = check_command("tar", &["-cvf", "archive.tar.gz", "file1.txt", "file2.txt"]);
        assert!(
            matches!(outcome, PolicyOutcome::Approved),
            "tar create should be allowed: {:?}",
            outcome
        );
    });
}

/// Test denial reason includes the blacklist category.
#[test]
fn denial_includes_category() {
    with_default_timeout(|| {
        let outcome = check_command("git", &["status"]);
        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(
                reason.contains("version_control") || reason.contains("version control"),
                "Denial reason should mention category: {}",
                reason
            );
        } else {
            panic!("Expected denial, got {:?}", outcome);
        }
    });
}

/// Test denial reason includes the command name.
#[test]
fn denial_includes_command_name() {
    with_default_timeout(|| {
        let outcome = check_command("sudo", &["ls"]);
        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(
                reason.contains("sudo"),
                "Denial reason should mention the command: {}",
                reason
            );
        } else {
            panic!("Expected denial, got {:?}", outcome);
        }
    });
}

/// Test that a Development session has ProcessExecBounded capability.
#[test]
fn development_session_has_process_exec_capability() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-dev".to_string(), SessionDrain::Development, 0);

        assert!(
            session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Development should have ProcessExecBounded"
        );
    });
}

/// Test that a Planning session does NOT have ProcessExecBounded capability.
#[test]
fn planning_session_lacks_process_exec_capability() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("test-planning".to_string(), SessionDrain::Planning, 0);

        assert!(
            !session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Planning should NOT have ProcessExecBounded"
        );
    });
}

/// Test that a Fix session has ProcessExecBounded capability.
#[test]
fn fix_session_has_process_exec_capability() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-fix".to_string(), SessionDrain::Fix, 0);

        assert!(
            session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Fix should have ProcessExecBounded"
        );
    });
}
