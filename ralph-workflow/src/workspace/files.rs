// WorkspaceFs - Production filesystem implementation of the Workspace trait.
//
// Filesystem I/O lives in `files/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("files/io.rs");
