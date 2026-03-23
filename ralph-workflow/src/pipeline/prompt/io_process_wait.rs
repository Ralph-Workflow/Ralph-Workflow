// io_process_wait — production implementation.
//
// All I/O lives in `io_process_wait/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("io_process_wait/io.rs");
