// Core rebase conflict detection and marker extraction — production implementation.
//
// All I/O lives in `rebase_conflicts/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("rebase_conflicts/io.rs");
