// Core rebase operations: abort — production implementation.
//
// All I/O lives in `rebase_abort/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("rebase_abort/io.rs");
