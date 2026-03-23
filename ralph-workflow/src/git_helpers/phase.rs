// Agent phase lifecycle management — production implementation.
//
// All I/O lives in `phase/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("phase/io.rs");
