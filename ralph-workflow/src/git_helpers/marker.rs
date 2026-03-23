// Marker file creation and repair for agent-phase commit protection — production implementation.
//
// All I/O lives in `marker/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("marker/io.rs");
