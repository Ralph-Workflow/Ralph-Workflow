// exit_pause — production implementation.
//
// All I/O lives in `exit_pause/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("exit_pause/io.rs");
