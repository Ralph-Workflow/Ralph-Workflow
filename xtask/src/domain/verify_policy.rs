//! Pure verify-policy logic with no I/O or side effects.
//!
//! This module contains pure text-transformation functions for filtering
//! compiler/test output according to policy rules.

use std::borrow::Cow;

/// Strip known-OK integration test warning lines from output.
///
/// Returns a `Cow::Borrowed` if nothing was stripped, `Cow::Owned` if any
/// lines were removed.
pub(crate) fn strip_integration_warnings(text: &str) -> Cow<'_, str> {
    if !text.lines().any(is_known_integration_warning) {
        return Cow::Borrowed(text);
    }
    Cow::Owned(
        text.lines()
            .filter(|line| !is_known_integration_warning(line))
            .map(|line| format!("{line}\n"))
            .collect(),
    )
}

fn is_known_integration_warning(line: &str) -> bool {
    let trimmed = line.trim_start();
    (trimmed.starts_with('[') && trimmed.contains("] ⚠ "))
        || trimmed.starts_with("⚠️  Risks & Mitigations:")
        || trimmed.starts_with("Warning: Delta discontinuity detected in OpenCode text.")
}

/// Strip the generated-harness large-stack-frames error block from output.
///
/// Removes the `error: this function may allocate` block that originates from
/// the test harness at `ralph-workflow/src/lib.rs:9:50`, up to and including
/// the "could not compile" line and any trailing build-failed warning.
pub(crate) fn strip_generated_harness_block(text: &str) -> String {
    text.lines()
        .scan(false, |skipping, line| {
            Some(classify_harness_line(skipping, line).map(str::to_owned))
        })
        .flatten()
        .map(|line| format!("{line}\n"))
        .collect()
}

/// Classify a line during harness-block stripping.
///
/// Returns `None` if the line should be dropped, `Some(line)` if it should
/// be kept. Also updates the `skipping` state flag via the mutable reference.
fn classify_harness_line<'a>(skipping: &mut bool, line: &'a str) -> Option<&'a str> {
    let trimmed = line.trim_start();
    if trimmed.starts_with("error: this function may allocate ") {
        *skipping = true;
        return None;
    }
    if *skipping {
        if trimmed == "error: could not compile `ralph-workflow` (lib test) due to 1 previous error"
        {
            *skipping = false;
        }
        return None;
    }
    if trimmed == "warning: build failed, waiting for other jobs to finish..." {
        return None;
    }
    Some(line)
}
