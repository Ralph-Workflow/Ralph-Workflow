// cache_environment — production implementation.
//
// All I/O lives in `cache_environment/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("cache_environment/io.rs");
