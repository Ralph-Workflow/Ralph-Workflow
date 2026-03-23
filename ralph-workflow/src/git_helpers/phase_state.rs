// Process-global state for the agent phase lifecycle — production implementation.
//
// All I/O lives in `phase_state/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("phase_state/io.rs");
