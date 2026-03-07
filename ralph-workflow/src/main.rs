// DO NOT CHANGE THESE CLIPPY SETTINGS, YOU MUST REFACTOR INSTEAD, EVEN IF IT TAKES YOU 100 YEARS
// Note: clippy::cargo is not enabled because it flags transitive dependency version conflicts
// (e.g., bitflags 1.3.2 from inotify vs 2.10.0 from other crates) which are ecosystem-level
// issues outside our control and don't reflect code quality problems.
#![deny(warnings, unsafe_code, clippy::all, clippy::pedantic, clippy::nursery)]
//! Ralph: PROMPT-driven agent loop for git repos
//!
//! Runs:
//! - Developer agent: iterative progress against PROMPT.md
//! - Reviewer agent: review → fix → review passes
//! - Optional fast/full checks
//! - Final `git add -A` + `git commit -m <msg>`

use clap::Parser;
use ralph_workflow::app;
use ralph_workflow::cli::Args;
use ralph_workflow::exit_pause;
use ralph_workflow::interrupt;
use ralph_workflow::RealProcessExecutor;

fn main() -> anyhow::Result<()> {
    // Set up Ctrl+C handler for graceful checkpoint save on interrupt
    interrupt::setup_interrupt_handler();

    // Create real process executor for production use
    let args = Args::parse();
    let pause_mode = args.pause_on_exit;
    let executor = std::sync::Arc::new(RealProcessExecutor::new());
    let result = app::run(args, executor);

    let interrupted = interrupt::take_exit_130_after_run();
    let outcome = if interrupted {
        exit_pause::ExitOutcome::Interrupted
    } else if result.is_err() {
        exit_pause::ExitOutcome::Failure
    } else {
        exit_pause::ExitOutcome::Success
    };

    let launch_context = exit_pause::detect_launch_context();
    if exit_pause::should_pause_before_exit(pause_mode, outcome, &launch_context) {
        let _ = exit_pause::pause_for_enter();
    }

    // If the pipeline requested a SIGINT exit code, exit after cleanup has completed.
    if interrupted {
        std::process::exit(130);
    }

    result
}
