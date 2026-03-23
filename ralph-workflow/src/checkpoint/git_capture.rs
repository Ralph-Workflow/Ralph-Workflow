// git_capture — production implementation.
//
// All I/O lives in `git_capture/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("git_capture/io.rs");
