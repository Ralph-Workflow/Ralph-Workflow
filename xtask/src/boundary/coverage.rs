//! Boundary layer for coverage: runs `cargo llvm-cov` commands in diagnostic mode.
//!
//! Coverage is informational only. Both commands are run unconditionally, and
//! the overall exit is always `ExitCode::SUCCESS` regardless of whether the
//! coverage tooling succeeds. Coverage is **never** a build gate.

use std::process::ExitCode;

/// Run both llvm-cov commands and always exit 0 (diagnostic, non-gating).
///
/// Sequence:
/// 1. `cargo llvm-cov --all-features --lib -p ralph-workflow --html
///       --output-dir target/coverage/html`
/// 2. `cargo llvm-cov report --lib -p ralph-workflow`
///
/// If either command fails (non-zero exit or launch error), a diagnostic
/// message is emitted and execution continues. The subcommand always exits 0.
pub fn run_coverage() -> ExitCode {
    eprintln!("=== cargo xtask coverage (diagnostic only — non-gating) ===");
    eprintln!("[coverage] coverage is informational; exit is 0 regardless of result");

    run_single_command(
        "llvm-cov --all-features --lib -p ralph-workflow --html --output-dir target/coverage/html",
        &[
            "llvm-cov",
            "--all-features",
            "--lib",
            "-p",
            "ralph-workflow",
            "--html",
            "--output-dir",
            "target/coverage/html",
        ],
    );

    run_single_command(
        "llvm-cov report --lib -p ralph-workflow",
        &["llvm-cov", "report", "--lib", "-p", "ralph-workflow"],
    );

    eprintln!("[coverage] complete — exit 0 (diagnostic, non-gating)");
    ExitCode::SUCCESS
}

/// Run a single cargo subcommand, logging the result without propagating failure.
fn run_single_command(label: &str, args: &[&str]) {
    eprintln!("[coverage] running: cargo {label}");
    let outcome = std::process::Command::new("cargo").args(args).status();
    let err_string = outcome.as_ref().err().map(|e| e.to_string());
    let succeeded = outcome.as_ref().map(|s| s.success()).unwrap_or(false);
    let msg = coverage_log_line(label, succeeded, err_string.as_deref());
    eprintln!("{msg}");
}

/// Format a diagnostic log line for a coverage command result.
///
/// Pure helper exposed for unit-testing the non-gating messaging contract.
/// Returns a human-readable line that always makes the non-gating intent
/// explicit when the command did not succeed.
pub(crate) fn coverage_log_line(cmd_label: &str, succeeded: bool, err: Option<&str>) -> String {
    if let Some(e) = err {
        format!(
            "[coverage] {cmd_label}: failed to launch ({e}) — diagnostic only, non-gating, continuing"
        )
    } else if succeeded {
        format!("[coverage] {cmd_label}: ok")
    } else {
        format!(
            "[coverage] {cmd_label}: exited with non-zero — diagnostic only, non-gating, continuing"
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coverage_log_line_success_is_concise() {
        let msg = coverage_log_line("llvm-cov --html", true, None);
        assert_eq!(msg, "[coverage] llvm-cov --html: ok");
    }

    #[test]
    fn coverage_log_line_failure_communicates_non_gating() {
        let msg = coverage_log_line("llvm-cov --html", false, None);
        assert!(
            msg.contains("non-gating"),
            "failure message must state non-gating: {msg}"
        );
        assert!(
            msg.contains("diagnostic only"),
            "failure message must state diagnostic: {msg}"
        );
        assert!(
            msg.contains("continuing"),
            "failure message must indicate continuation: {msg}"
        );
    }

    #[test]
    fn coverage_log_line_launch_error_communicates_non_gating() {
        let msg = coverage_log_line("llvm-cov report", false, Some("No such file or directory"));
        assert!(
            msg.contains("non-gating"),
            "launch error message must state non-gating: {msg}"
        );
        assert!(
            msg.contains("diagnostic only"),
            "launch error message must state diagnostic: {msg}"
        );
        assert!(
            msg.contains("No such file or directory"),
            "error detail must appear in message: {msg}"
        );
    }

    #[test]
    fn coverage_log_line_err_branch_takes_priority_over_succeeded_false() {
        // Even if succeeded=false, when err is Some the message uses the "failed to launch" branch
        let msg = coverage_log_line("llvm-cov report", false, Some("permission denied"));
        assert!(
            msg.contains("failed to launch"),
            "err branch must be taken when err is Some: {msg}"
        );
        assert!(
            msg.contains("permission denied"),
            "error detail must appear: {msg}"
        );
    }
}
