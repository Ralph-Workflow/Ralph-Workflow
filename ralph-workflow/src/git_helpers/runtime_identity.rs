// Runtime identity — environment variable access — production implementation.
//
// All I/O lives in `runtime_identity/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("runtime_identity/io.rs");
