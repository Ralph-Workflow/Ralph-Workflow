// Lint policy: fix tooling code to match the style guide before considering an
// exception.
//
// See `CODE_STYLE.md`, `docs/code-style/boundaries.md`,
// `docs/code-style/coding-patterns.md`, `docs/code-style/testing.md`, and
// `xtask/clippy.toml` for crate-specific rationale.

#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]
#![deny(
    // No explicit iterator loops when a more idiomatic form exists
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    // `xtask` is a build-tooling boundary: keep universal correctness rules here,
    // and document any remaining boundary-only exceptions in `xtask/clippy.toml`.
    clippy::panic_in_result_fn,
    clippy::print_stdout,
    clippy::dbg_macro,
    // Push toward combinators instead of hand-written control flow
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    // needless_collect is removed from deny - the collect IS needed for parallelism
    // (collecting handles before joining ensures all threads are spawned before any join)
)]

mod boundary;
mod domain;
mod io;
mod runtime;

use std::process::ExitCode;

fn main() -> ExitCode {
    runtime::subcommand::run_from_env()
}
