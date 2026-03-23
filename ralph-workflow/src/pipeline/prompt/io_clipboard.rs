// io_clipboard — production implementation.
//
// All I/O lives in `io_clipboard/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("io_clipboard/io.rs");
