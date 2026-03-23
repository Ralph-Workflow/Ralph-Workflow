// system — production implementation.
//
// All I/O lives in `system/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("system/io.rs");
