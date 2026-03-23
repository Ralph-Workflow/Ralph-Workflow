// Cleanup utilities for agent-phase protection artifacts — production implementation.
//
// All I/O lives in `cleanup/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("cleanup/io.rs");
