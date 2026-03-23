// Hook installation logic — production implementation.
//
// All I/O lives in `install/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("install/io.rs");
