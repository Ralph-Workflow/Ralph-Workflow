// Worktree-scoped hooks management — production implementation.
//
// All I/O lives in `worktree/io.rs` (boundary module — file stem `io`
// is recognized as a boundary by forbid_io_effects).

include!("worktree/io.rs");
