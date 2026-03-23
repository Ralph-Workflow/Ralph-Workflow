// Git repository discovery and protection scope — production implementation.
//
// All I/O lives in `discovery/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("discovery/io.rs");
