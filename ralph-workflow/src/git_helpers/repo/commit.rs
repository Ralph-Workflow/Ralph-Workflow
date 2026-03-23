// Git commit and staging operations — production implementation.
//
// All I/O lives in `commit/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("commit/io.rs");
