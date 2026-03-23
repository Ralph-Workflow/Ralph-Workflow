// timer — production implementation.
//
// All I/O lives in `timer/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("timer/io.rs");
