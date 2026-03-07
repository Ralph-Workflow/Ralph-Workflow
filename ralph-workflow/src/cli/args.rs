//! CLI argument definitions.
//!
//! Contains the `Args` struct with clap configuration for command-line parsing.

use clap::Parser;

include!("args/verbosity.rs");
include!("args/presets.rs");
include!("args/unified_init.rs");
include!("args/listing.rs");
include!("args/completion.rs");
include!("args/recovery.rs");
include!("args/templates.rs");
include!("args/commit.rs");
include!("args/pause_on_exit.rs");
include!("args/args_struct.rs");
