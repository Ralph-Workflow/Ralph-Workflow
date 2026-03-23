// clock — production implementation.
//
// All I/O lives in `clock/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("clock/io.rs");
