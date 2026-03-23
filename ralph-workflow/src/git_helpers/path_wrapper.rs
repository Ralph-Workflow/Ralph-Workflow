// PATH wrapper operations for git commit protection — production implementation.
//
// All I/O lives in `path_wrapper/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("path_wrapper/io.rs");
