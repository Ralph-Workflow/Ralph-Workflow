// Git local exclude management — production implementation.
//
// All I/O lives in `exclude/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("exclude/io.rs");
