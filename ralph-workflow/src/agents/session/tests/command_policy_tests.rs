//! Tests for command policy blacklist enforcement.
//!
//! These tests verify that the command policy correctly denies blacklisted commands
//! and allows non-blacklisted commands across all blacklist categories.

use crate::agents::session::command_policy::check_command;
use crate::agents::session::PolicyOutcome;

/// Helper to assert a command is denied with a reason containing expected text.
fn assert_denied(command: &str, args: &[&str], expected_in_reason: &str) {
    let outcome = check_command(command, args);
    match &outcome {
        PolicyOutcome::Denied { reason } => {
            assert!(
                reason.contains(expected_in_reason),
                "Expected reason to contain '{}', got: {}",
                expected_in_reason,
                reason
            );
        }
        PolicyOutcome::Approved => {
            panic!(
                "Expected command '{}' to be denied, but it was approved",
                command
            );
        }
        PolicyOutcome::ApprovedWithRestriction { restriction } => {
            panic!(
                "Expected command '{}' to be denied, but it was approved with restriction: {}",
                command, restriction
            );
        }
    }
}

/// Helper to assert a command is approved.
fn assert_approved(command: &str, args: &[&str]) {
    let outcome = check_command(command, args);
    match &outcome {
        PolicyOutcome::Approved => {}
        PolicyOutcome::Denied { reason } => {
            panic!(
                "Expected command '{}' to be approved, but it was denied: {}",
                command, reason
            );
        }
        PolicyOutcome::ApprovedWithRestriction { restriction } => {
            panic!(
                "Expected command '{}' to be approved (not with restriction), but got restriction: {}",
                command, restriction
            );
        }
    }
}

// =============================================================================
// Version Control - Blacklisted
// =============================================================================

#[test]
fn deny_git_commands() {
    assert_denied("git", &["commit", "-m", "fix"], "version control");
    assert_denied("git", &["push"], "version control");
    assert_denied("git", &["pull"], "version control");
    assert_denied("git", &["clone"], "version control");
}

#[test]
fn deny_svn_commands() {
    assert_denied("svn", &["update"], "version control");
    assert_denied("svn", &["commit"], "version control");
}

#[test]
fn deny_hg_commands() {
    assert_denied("hg", &["push"], "version control");
    assert_denied("hg", &["pull"], "version control");
}

#[test]
fn deny_other_vcs_commands() {
    assert_denied("fossil", &["commit"], "version control");
    assert_denied("bzr", &["commit"], "version control");
    assert_denied("darcs", &["push"], "version control");
}

// =============================================================================
// Version Control - Allowed
// =============================================================================

#[test]
fn allow_build_tools() {
    assert_approved("cargo", &["build"]);
    assert_approved("cargo", &["test"]);
    assert_approved("cargo", &["check"]);
    assert_approved("rustc", &["--version"]);
    assert_approved("gcc", &["--version"]);
    assert_approved("make", &["build"]);
}

// =============================================================================
// Privilege Escalation - Blacklisted
// =============================================================================

#[test]
fn deny_privilege_escalation() {
    assert_denied("sudo", &[], "privilege escalation");
    assert_denied(
        "sudo",
        &["apt-get", "install", "foo"],
        "privilege escalation",
    );
    assert_denied("su", &[], "privilege escalation");
    assert_denied("doas", &[], "privilege escalation");
    assert_denied("pkexec", &[], "privilege escalation");
    assert_denied("runuser", &[], "privilege escalation");
}

// =============================================================================
// Destructive System - Blacklisted
// =============================================================================

#[test]
fn deny_rm_rf_root() {
    assert_denied("rm", &["-rf", "/"], "destructive system");
    assert_denied("rm", &["-rf", "/home"], "destructive system");
    assert_denied("rm", &["-rf", "/."], "destructive system");
    assert_denied("rm", &["-rf", "~"], "destructive system");
}

#[test]
fn deny_shutdown_commands() {
    assert_denied("shutdown", &[], "destructive system");
    assert_denied("reboot", &[], "destructive system");
    assert_denied("halt", &[], "destructive system");
    assert_denied("poweroff", &[], "destructive system");
}

#[test]
fn deny_killall() {
    assert_denied("killall", &[], "destructive system");
}

#[test]
fn deny_kill_init() {
    assert_denied("kill", &["-9", "1"], "destructive system");
}

#[test]
fn deny_device_targets() {
    assert_denied(
        "dd",
        &["if=/dev/zero", "of=/dev/null"],
        "destructive system",
    );
    assert_denied("mkfs", &["-t", "ext4", "/dev/sda1"], "destructive system");
}

// =============================================================================
// Destructive System - Allowed
// =============================================================================

#[test]
fn allow_rm_normal_files() {
    assert_approved("rm", &["file.txt"]);
    assert_approved("rm", &["-f", "file.txt"]);
    assert_approved("rm", &["-r", "dir"]);
}

// =============================================================================
// Network Exfiltration - Blacklisted
// =============================================================================

#[test]
fn deny_curl_external() {
    assert_denied("curl", &["https://evil.com"], "network");
    assert_denied("curl", &["-s", "https://external.host"], "network");
}

#[test]
fn deny_wget_external() {
    assert_denied("wget", &["https://evil.com"], "network");
    assert_denied("wget", &["-r", "https://external.host"], "network");
}

#[test]
fn deny_netcat() {
    assert_denied("nc", &[], "network");
    assert_denied("ncat", &[], "network");
    assert_denied("netcat", &[], "network");
    assert_denied("socat", &[], "network");
}

#[test]
fn deny_ssh_scp() {
    assert_denied("ssh", &["user@host"], "network");
    assert_denied("scp", &["file.txt", "user@host:/path"], "network");
    assert_denied("rsync", &["-av", "src/", "user@host:/dest"], "network");
}

// =============================================================================
// Network Exfiltration - Allowed (localhost)
// =============================================================================

#[test]
fn allow_curl_localhost() {
    assert_approved("curl", &["http://localhost:8080"]);
    assert_approved("curl", &["http://127.0.0.1:8080"]);
    assert_approved("curl", &["localhost:3000"]);
    assert_approved("curl", &["127.0.0.1"]);
}

// =============================================================================
// Package Manager - Blacklisted
// =============================================================================

#[test]
fn deny_package_managers() {
    assert_denied("apt", &["install", "foo"], "package manager");
    assert_denied("yum", &["install", "foo"], "package manager");
    assert_denied("dnf", &["install", "foo"], "package manager");
    assert_denied("pacman", &["-S", "foo"], "package manager");
    assert_denied("brew", &["install", "foo"], "package manager");
}

#[test]
fn deny_global_pip_npm() {
    assert_denied("pip", &["install", "--user", "foo"], "package manager");
    assert_denied("npm", &["install", "-g", "foo"], "package manager");
    assert_denied("pip3", &["install", "--user", "foo"], "package manager");
}

// =============================================================================
// Package Manager - Allowed
// =============================================================================

#[test]
fn allow_pip_npm_local() {
    assert_approved("pip", &["install", "foo"]);
    assert_approved("npm", &["install", "foo"]);
    assert_approved("cargo", &["build"]);
    assert_approved("cargo", &["test"]);
}

// =============================================================================
// Container Escape - Blacklisted
// =============================================================================

#[test]
fn deny_container_commands() {
    assert_denied("docker", &["run", "-it", "ubuntu"], "container");
    assert_denied("docker", &["exec"], "container");
    assert_denied("podman", &["run"], "container");
}

#[test]
fn deny_chroot_nsenter() {
    assert_denied("chroot", &[], "container");
    assert_denied("nsenter", &[], "container");
    assert_denied("unshare", &[], "container");
}

// =============================================================================
// Multi-File Operations - Blacklisted
// =============================================================================

#[test]
fn deny_find_exec_rm() {
    assert_denied(
        "find",
        &["/", "-exec", "rm", "-rf", "{}", ";"],
        "multi-file",
    );
}

#[test]
fn deny_sed_inplace() {
    assert_denied("sed", &["-i", "s/foo/bar/g", "*.txt"], "multi-file");
}

#[test]
fn deny_chmod_chown_recursive() {
    assert_denied("chmod", &["-R", "777", "/path"], "multi-file");
    assert_denied("chown", &["-R", "user:group", "/path"], "multi-file");
}

#[test]
fn deny_cp_r_with_globs() {
    assert_denied("cp", &["-r", "src/*", "dest"], "multi-file");
}

#[test]
fn deny_tar_extract() {
    assert_denied("tar", &["-xvf", "archive.tar"], "multi-file");
    assert_denied("unzip", &["-d", "output_dir", "file.zip"], "multi-file");
}

// =============================================================================
// Multi-File Operations - Allowed
// =============================================================================

#[test]
fn allow_find_readonly() {
    assert_approved("find", &[".", "-name", "*.txt"]);
    assert_approved("find", &["/", "-maxdepth", "1", "-name", "*.txt"]);
}

#[test]
fn allow_sed_without_i() {
    assert_approved("sed", &["s/foo/bar/g", "file.txt"]);
}

#[test]
fn allow_cp_without_r_with_glob() {
    assert_approved("cp", &["file1.txt", "file2.txt", "dest/"]);
}
