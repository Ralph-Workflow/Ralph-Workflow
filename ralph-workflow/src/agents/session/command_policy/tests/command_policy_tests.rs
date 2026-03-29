//! Tests for command_policy.rs - Blacklist-based command filtering.

use crate::agents::session::command_policy::check_command;
use crate::agents::session::PolicyOutcome;

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
    let tokens = crate::agents::session::command_policy::parse_command("git status");
    assert_eq!(tokens, vec!["git", "status"]);
}

#[test]
fn parse_command_with_flags() {
    let tokens = crate::agents::session::command_policy::parse_command("cargo test --lib");
    assert_eq!(tokens, vec!["cargo", "test", "--lib"]);
}

#[test]
fn parse_quoted_args() {
    let tokens =
        crate::agents::session::command_policy::parse_command("git commit -m \"fix: bug\"");
    assert_eq!(tokens, vec!["git", "commit", "-m", "fix: bug"]);
}

#[test]
fn parse_single_quoted_args() {
    let tokens = crate::agents::session::command_policy::parse_command("echo 'hello world'");
    assert_eq!(tokens, vec!["echo", "hello world"]);
}

#[test]
fn parse_command_with_pipe() {
    let tokens = crate::agents::session::command_policy::parse_command("ls | grep foo");
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
