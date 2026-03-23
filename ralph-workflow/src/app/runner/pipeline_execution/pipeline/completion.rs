// Pipeline Completion Handling
//
// This module handles defensive completion marker writing for external orchestration.
//
// Purpose:
//
// The completion marker is written to `.agent/tmp/completion_marker` to signal
// pipeline termination to external orchestrators. This is especially important
// for abnormal terminations where the event loop exits without reaching a
// terminal state.
//
// Defensive Writing:
//
// The `write_defensive_completion_marker` function is called when:
// - Event loop exits without `completed=true`
// - Final phase is unexpected (not Complete or Interrupted)
// - Event loop fails to write its own completion marker
//
// This ensures external systems can always detect termination, even if the
// event loop had bugs preventing normal completion flow.
//
// Marker Format:
//
// failure
// Event loop exited without normal completion (final_phase=AwaitingDevFix)
//
// The first line is the status (`failure`), followed by a diagnostic message.

/// Writes a defensive completion marker when the event loop exits abnormally.
///
/// This function is called as a fallback when the event loop exits without
/// normal completion (e.g., `completed=false` or unexpected final phase).
///
/// # Purpose
///
/// External orchestrators rely on `.agent/tmp/completion_marker` to detect
/// pipeline termination. If the event loop fails to write this marker due to
/// bugs or unexpected state transitions, this defensive write ensures the
/// marker always exists.
///
/// # Marker Content
///
/// Writes a two-line marker:
/// - Line 1: "failure" (status)
/// - Line 2: Diagnostic message including the final phase
///
/// # Errors
///
/// Returns `false` if:
/// - Directory creation fails (`.agent/tmp`)
/// - File write fails (`.agent/tmp/completion_marker`)
///
/// Errors are logged but not returned as errors since this is defensive code.
///
/// # Examples
///
/// See the unit tests in the `completion_tests` module for working examples
/// of how this function behaves in different scenarios.
pub(crate) fn write_defensive_completion_marker(
    workspace: &dyn crate::workspace::Workspace,
    logger: &crate::logger::Logger,
    final_phase: crate::reducer::event::PipelinePhase,
) -> bool {
    if let Err(err) = workspace.create_dir_all(std::path::Path::new(".agent/tmp")) {
        logger.error(&format!(
            "Failed to create completion marker directory: {err}"
        ));
        return false;
    }

    let marker_path = std::path::Path::new(".agent/tmp/completion_marker");
    let content = format!(
        "failure\nEvent loop exited without normal completion (final_phase={final_phase:?})"
    );
    if let Err(err) = workspace.write(marker_path, &content) {
        logger.error(&format!(
            "Failed to write defensive completion marker: {err}"
        ));
        return false;
    }

    logger.info("Defensive completion marker written: failure");
    true
}

#[cfg(test)]
mod completion_tests {
    use super::*;
    use crate::workspace::{DirEntry, MemoryWorkspace, Workspace};
    use std::io;
    use std::path::{Path, PathBuf};
    use std::sync::Mutex;

    /// Test workspace that tracks whether `.agent/tmp` was created.
    ///
    /// This allows us to verify that `write_defensive_completion_marker`
    /// creates the directory before writing the marker file.
    #[derive(Debug)]
    struct TrackingWorkspace {
        inner: MemoryWorkspace,
        tmp_created: Mutex<bool>,
    }

    impl TrackingWorkspace {
        fn new() -> Self {
            Self {
                inner: MemoryWorkspace::new(PathBuf::from("/test/repo")),
                tmp_created: Mutex::new(false),
            }
        }

        fn tmp_created(&self) -> bool {
            *self.tmp_created.lock().expect("mutex poisoned")
        }
    }

    impl crate::workspace::Workspace for TrackingWorkspace {
        fn root(&self) -> &Path {
            self.inner.root()
        }

        fn read(&self, relative: &Path) -> io::Result<String> {
            self.inner.read(relative)
        }

        fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
            self.inner.read_bytes(relative)
        }

        fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write(relative, content)
        }

        fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.write_bytes(relative, content)
        }

        fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.append_bytes(relative, content)
        }

        fn exists(&self, relative: &Path) -> bool {
            self.inner.exists(relative)
        }

        fn is_file(&self, relative: &Path) -> bool {
            self.inner.is_file(relative)
        }

        fn is_dir(&self, relative: &Path) -> bool {
            self.inner.is_dir(relative)
        }

        fn remove(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove(relative)
        }

        fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_if_exists(relative)
        }

        fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all(relative)
        }

        fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all_if_exists(relative)
        }

        fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
            if relative == Path::new(".agent/tmp") {
                *self.tmp_created.lock().expect("mutex poisoned") = true;
            }
            self.inner.create_dir_all(relative)
        }

        fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
            self.inner.read_dir(relative)
        }

        fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
            self.inner.rename(from, to)
        }

        fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write_atomic(relative, content)
        }

        fn set_readonly(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_readonly(relative)
        }

        fn set_writable(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_writable(relative)
        }
    }

    #[test]
    fn test_defensive_completion_marker_creates_tmp_dir() {
        let workspace = TrackingWorkspace::new();
        let logger = crate::logger::Logger::new(crate::logger::Colors { enabled: false });

        let wrote = write_defensive_completion_marker(
            &workspace,
            &logger,
            crate::reducer::event::PipelinePhase::AwaitingDevFix,
        );

        assert!(wrote, "marker write should succeed");
        assert!(
            workspace.tmp_created(),
            "should create .agent/tmp before writing marker"
        );
        assert!(
            workspace.exists(Path::new(".agent/tmp/completion_marker")),
            "completion marker should exist after defensive write"
        );
    }

    #[test]
    fn test_defensive_completion_marker_content_format() {
        let workspace = MemoryWorkspace::new(PathBuf::from("/test/repo"));
        let logger = crate::logger::Logger::new(crate::logger::Colors { enabled: false });

        let wrote = write_defensive_completion_marker(
            &workspace,
            &logger,
            crate::reducer::event::PipelinePhase::Development,
        );

        assert!(wrote, "marker write should succeed");
        let content = workspace
            .read(Path::new(".agent/tmp/completion_marker"))
            .expect("should read marker");

        assert!(
            content.starts_with("failure\n"),
            "marker should start with failure status"
        );
        assert!(
            content.contains("Development"),
            "marker should include final phase"
        );
    }
}
