use super::super::file_activity::FileActivityTracker;
use crate::workspace::{DirEntry, MemoryWorkspace, Workspace};
use std::io;
use std::path::Path;
use std::time::{Duration, SystemTime};

#[test]
fn test_detects_recent_plan_md_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Plan content");

    // Recent file should be detected
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "PLAN.md modified recently should be detected"
    );
}

#[test]
fn test_detects_recent_issues_md_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/ISSUES.md", "# Issues");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "ISSUES.md modified recently should be detected"
    );
}

#[test]
fn test_detects_recent_notes_md_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/NOTES.md", "# Notes");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "NOTES.md modified recently should be detected"
    );
}

#[test]
fn test_detects_recent_status_md_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/STATUS.md", "# Status");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "STATUS.md modified recently should be detected"
    );
}

#[test]
fn test_detects_recent_commit_message_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/commit-message.txt", "commit msg");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "commit-message.txt modified recently should be detected"
    );
}

#[test]
fn test_detects_xml_artifacts_in_tmp() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/tmp/output.xml", "<xml/>");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "XML files in .agent/tmp/ should be detected"
    );
}

#[test]
fn test_ignores_log_files() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test()
        .with_file(".agent/pipeline.log", "log content")
        .with_file(".agent/debug.log", "debug logs");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "Log files should not count as activity"
    );
}

#[test]
fn test_ignores_checkpoint_json() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/checkpoint.json", "{}");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "checkpoint.json should not count as activity"
    );
}

#[test]
fn test_ignores_start_commit() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/start_commit", "abc123");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "start_commit should not count as activity"
    );
}

#[test]
fn test_ignores_review_baseline_txt() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/review_baseline.txt", "baseline");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "review_baseline.txt should not count as activity"
    );
}

#[test]
fn test_ignores_old_modifications() {
    let tracker = FileActivityTracker::new();
    let old_time = SystemTime::now() - Duration::from_secs(400);
    let ws =
        MemoryWorkspace::new_test().with_file_at_time(".agent/PLAN.md", "old content", old_time);

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "Files modified >300s ago should not be detected"
    );
}

#[test]
fn test_ignores_editor_temp_files() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test()
        .with_file(".agent/.PLAN.md.swp", "swap")
        .with_file(".agent/PLAN.md.tmp", "temp")
        .with_file(".agent/PLAN.md~", "backup")
        .with_file(".agent/PLAN.md.bak", "backup");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "Editor temporary files should not count as activity"
    );
}

#[test]
fn test_handles_missing_agent_directory_gracefully() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test();

    // Should not panic, should return Ok(false)
    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "Missing .agent/ directory should return false, not error"
    );
}

#[derive(Debug)]
struct RootReadDirFailsWorkspace {
    inner: MemoryWorkspace,
}

impl RootReadDirFailsWorkspace {
    fn new(inner: MemoryWorkspace) -> Self {
        Self { inner }
    }
}

impl Workspace for RootReadDirFailsWorkspace {
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
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
        if relative.as_os_str().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "root read_dir denied",
            ));
        }
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
fn test_workspace_scan_read_dir_error_is_not_silently_ignored() {
    // If the workspace root cannot be scanned, the tracker must return an error so
    // the monitor can emit a warning that distinguishes "no activity" from
    // "could not scan".
    let tracker = FileActivityTracker::new();
    let ws = RootReadDirFailsWorkspace::new(MemoryWorkspace::new_test());

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .is_err(),
        "workspace scan errors should be surfaced as Err, not treated as no activity"
    );
}

#[test]
fn test_tracks_modification_state_across_calls() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "initial");

    // First check should detect the file
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "First check should detect new file"
    );

    // Second check should still detect activity because the file is still
    // within the recency window, even if it has not changed since last check.
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "Second check should still detect recent activity"
    );
}

#[test]
fn test_detects_new_modification_after_initial_check() {
    let tracker = FileActivityTracker::new();

    // Create workspace with old file
    let old_time = SystemTime::now() - Duration::from_secs(100);
    let ws =
        MemoryWorkspace::new_test().with_file_at_time(".agent/PLAN.md", "old content", old_time);

    // First check
    tracker
        .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
        .unwrap();

    // Simulate file update by clearing and recreating with new mtime
    ws.clear();
    let new_time = SystemTime::now() - Duration::from_secs(10);
    let ws = ws.with_file_at_time(".agent/PLAN.md", "new content", new_time);

    // Second check should detect the modification
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "Should detect file modification with newer mtime"
    );
}

#[test]
fn test_multiple_ai_files_any_can_trigger() {
    let tracker = FileActivityTracker::new();

    // Create old PLAN.md
    let old_time = SystemTime::now() - Duration::from_secs(400);
    let ws = MemoryWorkspace::new_test()
        .with_file_at_time(".agent/PLAN.md", "old", old_time)
        .with_file(".agent/ISSUES.md", "recent"); // This one is recent

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "Should detect activity if ANY AI file is recent"
    );
}

#[test]
fn test_xml_files_only_in_tmp_subdirectory() {
    let tracker = FileActivityTracker::new();

    // XML in .agent/ root should not be detected
    let ws = MemoryWorkspace::new_test().with_file(".agent/output.xml", "<xml/>");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "XML files in .agent/ root should not be tracked"
    );
}

#[test]
fn test_non_xml_files_in_tmp_ignored() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/output.txt", "text")
        .with_file(".agent/tmp/data.json", "{}");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "Non-XML files in .agent/tmp/ should not be tracked"
    );
}

#[test]
fn test_custom_timeout_window() {
    let tracker = FileActivityTracker::new();

    // File modified 150 seconds ago
    let time_150s_ago = SystemTime::now() - Duration::from_secs(150);
    let ws =
        MemoryWorkspace::new_test().with_file_at_time(".agent/PLAN.md", "content", time_150s_ago);

    // With 300s timeout, should be detected
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "File modified 150s ago should be detected with 300s timeout"
    );

    // With 100s timeout, should not be detected
    let tracker2 = FileActivityTracker::new();
    assert!(
        !tracker2
            .check_for_recent_activity(&ws, Duration::from_secs(100), SystemTime::now())
            .unwrap(),
        "File modified 150s ago should not be detected with 100s timeout"
    );
}

#[test]
fn test_default_trait_implementation() {
    // Both constructors should work without errors
    let _tracker1 = FileActivityTracker::new();
    let _tracker2 = FileActivityTracker::default();
    // If we reach here, both constructors work correctly
}

#[test]
fn test_workspace_source_file_prevents_timeout() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("src/lib.rs", "fn main() {}");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "recently modified src/lib.rs with no .agent/ dir should be detected as activity"
    );
}

#[test]
fn test_old_workspace_source_file_does_not_prevent_timeout() {
    let tracker = FileActivityTracker::new();
    let old_time = SystemTime::now() - Duration::from_secs(400);
    let ws = MemoryWorkspace::new_test().with_file_at_time("src/lib.rs", "fn main() {}", old_time);

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "src/lib.rs modified 400s ago should not be detected with 300s timeout"
    );
}

#[test]
fn test_git_dir_excluded_from_workspace_scan() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".git/COMMIT_EDITMSG", "Initial commit");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        ".git/ files should be excluded from workspace scan"
    );
}

#[test]
fn test_target_dir_excluded_from_workspace_scan() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("target/debug/binary", "ELF binary");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "target/ files should be excluded from workspace scan"
    );
}

#[test]
fn test_workspace_log_file_excluded() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("pipeline.log", "2026-03-09 log output");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "*.log files at workspace root should be excluded from scan"
    );
}

#[test]
fn test_workspace_file_in_subdir_prevents_timeout() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("tests/integration.rs", "#[test] fn foo() {}");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "recently modified tests/integration.rs should be detected as activity"
    );
}

// Tests for deep directory detection (depth 2+).
// The current 1-level-deep workspace scan cannot find files nested inside
// subdirectories of direct root subdirectories (e.g., ralph-workflow/src/lib.rs).
// These tests MUST FAIL before the recursive scan is implemented.

#[test]
fn test_detects_file_at_depth_2() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("ralph-workflow/src/lib.rs", "pub fn foo() {}");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "recently modified ralph-workflow/src/lib.rs (depth 2) should be detected"
    );
}

#[test]
fn test_detects_file_at_depth_3() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(
        "ralph-workflow/src/pipeline/mod.rs",
        "pub mod idle_timeout;",
    );

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "recently modified ralph-workflow/src/pipeline/mod.rs (depth 3) should be detected"
    );
}

#[test]
fn test_detects_file_at_depth_4() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(
        "ralph-workflow/src/pipeline/idle_timeout/file_activity.rs",
        "// source",
    );

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "recently modified depth-4 file should be detected"
    );
}

#[test]
fn test_target_dir_excluded_at_any_depth() {
    let tracker = FileActivityTracker::new();
    let ws =
        MemoryWorkspace::new_test().with_file("ralph-workflow/target/debug/lib.rlib", "binary");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "target/ dir should be excluded even inside a workspace crate"
    );
}

#[test]
fn test_file_slightly_beyond_timeout_detected_with_wider_window() {
    // A file that is between timeout and timeout+check_interval old should NOT be
    // detected with the base timeout window but SHOULD be detected when the monitor
    // passes a wider window (actual_idle_time, which accounts for check_interval jitter).
    let tracker = FileActivityTracker::new();
    let timeout = Duration::from_secs(300);
    // File is 320s old: beyond the base 300s window but within 330s widened window.
    let file_time = SystemTime::now() - Duration::from_secs(320);
    let ws = MemoryWorkspace::new_test().with_file_at_time("src/lib.rs", "fn main() {}", file_time);

    // With base timeout window: not detected (320 > 300).
    assert!(
        !tracker
            .check_for_recent_activity(&ws, timeout, SystemTime::now())
            .unwrap(),
        "File 320s old should not be detected with 300s base window"
    );

    // With widened window (timeout + check_interval + overhead buffer): detected.
    // This is what the monitor now passes when actual_idle_time > timeout.
    let check_interval = Duration::from_secs(30);
    let overhead_buffer = Duration::from_secs(1);
    assert!(
        tracker
            .check_for_recent_activity(
                &ws,
                timeout + check_interval + overhead_buffer,
                SystemTime::now()
            )
            .unwrap(),
        "File 320s old must be detected with widened window (timeout + check_interval + buffer)"
    );
}

#[test]
fn test_detects_file_at_max_scan_depth_boundary() {
    // Regression test for an off-by-one bug in the recursive workspace scan.
    //
    // The scan depth is intended to be inclusive: when the remaining depth hits 0
    // we still scan the current directory but do not recurse further. The previous
    // implementation returned early and skipped scanning the directory at the
    // depth limit, missing real activity.
    let tracker = FileActivityTracker::new();

    // Depth 8 directory: d1/d2/d3/d4/d5/d6/d7/d8
    // If the scan incorrectly skips scanning at remaining_depth == 0, this file
    // will be missed.
    let ws = MemoryWorkspace::new_test()
        .with_file("d1/d2/d3/d4/d5/d6/d7/d8/file.rs", "pub fn touched() {}");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300), SystemTime::now())
            .unwrap(),
        "recent activity at the max scan depth must be detected"
    );
}

#[test]
fn test_future_mtime_counts_as_recent_activity() {
    // If the filesystem reports a future mtime (clock skew, network FS), treat the
    // file as fresh activity rather than extremely old.
    let tracker = FileActivityTracker::new();
    let future_time = SystemTime::now() + Duration::from_secs(60);
    let ws = MemoryWorkspace::new_test().with_file_at_time("src/lib.rs", "fn x() {}", future_time);

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(1), SystemTime::now())
            .unwrap(),
        "future mtimes should be treated as activity to avoid false idle kills"
    );
}
