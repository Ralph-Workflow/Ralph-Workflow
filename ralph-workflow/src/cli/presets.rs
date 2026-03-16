//! Preset configurations for common agent combinations.
//!
//! Presets allow users to quickly configure Ralph for common use cases
//! without specifying individual agent options.

use crate::config::{Config, ReviewDepth};
use crate::logger::Colors;
use clap::ValueEnum;

/// Preset configurations for common agent combinations.
#[derive(Clone, Debug, ValueEnum)]
pub enum Preset {
    /// Use `agent_chain` defaults (no explicit agent override)
    Default,
    /// Use opencode for both developer and reviewer
    Opencode,
}

/// Apply CLI arguments to the configuration.
///
/// This function uses the reducer architecture to process CLI arguments:
/// 1. Parse Args into a sequence of `CliEvents`
/// 2. Run events through the reducer to build `CliState`
/// 3. Apply `CliState` to Config
///
/// This approach ensures:
/// - All CLI arguments are handled (fixes missing -L, -S, -T flags)
/// - Event processing is testable and maintainable
/// - Consistent with the existing pipeline reducer pattern
#[expect(clippy::print_stderr, reason = "CLI warning for invalid arguments")]
pub fn apply_args_to_config(args: &super::Args, config: &mut Config, colors: Colors) {
    use crate::cli::reducer::{apply_cli_state_to_config, args_to_events, reduce, CliState};

    // Validate review depth before processing (for user warning)
    if let Some(ref depth) = args.review_depth {
        if ReviewDepth::from_str(depth).is_none() {
            eprintln!(
                "{}{}Warning:{} Unknown review depth '{}'. Using default (standard).",
                colors.bold(),
                colors.yellow(),
                colors.reset(),
                depth
            );
            eprintln!("Valid options: standard, comprehensive, security, incremental");
        }
    }

    // Parse args into events
    let events = args_to_events(args);

    // Run events through reducer to build state
    let mut state = CliState::initial();
    for event in events {
        state = reduce(state, event);
    }

    // Apply final state to config
    apply_cli_state_to_config(&state, config);
}
