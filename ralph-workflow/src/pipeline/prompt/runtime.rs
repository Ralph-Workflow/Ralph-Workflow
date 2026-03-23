// runtime — production implementation.
//
// All I/O lives in `runtime/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("runtime/io.rs");
