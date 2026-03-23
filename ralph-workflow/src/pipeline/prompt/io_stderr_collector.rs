// io_stderr_collector — production implementation.
//
// All I/O lives in `io_stderr_collector/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("io_stderr_collector/io.rs");
