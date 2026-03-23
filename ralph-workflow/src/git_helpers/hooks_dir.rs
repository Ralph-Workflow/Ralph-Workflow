// Hooks directory validation — production implementation.
//
// All I/O lives in `hooks_dir/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("hooks_dir/io.rs");
