// Persistent state and config management for git hooks — production implementation.
//
// All I/O lives in `config_state/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("config_state/io.rs");
