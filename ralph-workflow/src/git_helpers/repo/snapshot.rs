// Git snapshot and status operations — production implementation.
//
// All I/O lives in `snapshot/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("snapshot/io.rs");
