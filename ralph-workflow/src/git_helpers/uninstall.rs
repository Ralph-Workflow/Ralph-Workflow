// Hook uninstallation logic — production implementation.
//
// All I/O lives in `uninstall/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("uninstall/io.rs");
