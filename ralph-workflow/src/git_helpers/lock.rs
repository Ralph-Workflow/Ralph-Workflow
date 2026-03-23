// Rebase lock file management — production implementation.
//
// All I/O lives in `lock/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("lock/io.rs");
