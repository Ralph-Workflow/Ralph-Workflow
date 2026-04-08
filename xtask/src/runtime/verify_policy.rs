use std::borrow::Cow;

use crate::domain::verify_policy::{strip_generated_harness_block, strip_integration_warnings};

pub(crate) const FRONTEND_TEST_CHECK_NAME: &str = "ralph-gui-frontend-test";
pub(crate) const CLIPPY_CORE_CHECK_NAME: &str = "clippy-core";
pub(crate) const CORE_LIB_TEST_CHECK_NAME: &str = "test-ralph-workflow-lib";
pub(crate) const INTEGRATION_TEST_CHECK_NAME: &str = "test-integration";

pub(crate) fn strip_allowed_warning_lines_for_check<'a>(
    check_name: &str,
    text: &'a str,
) -> Cow<'a, str> {
    match check_name {
        FRONTEND_TEST_CHECK_NAME => strip_frontend_act_warnings(text),
        CORE_LIB_TEST_CHECK_NAME => strip_core_lib_warnings(text),
        INTEGRATION_TEST_CHECK_NAME => strip_integration_warnings(text),
        _ => Cow::Borrowed(text),
    }
}

fn strip_frontend_act_warnings(text: &str) -> Cow<'_, str> {
    if !text.contains("inside a test was not wrapped in act(...)") {
        return Cow::Borrowed(text);
    }

    Cow::Owned(build_frontend_act_filtered(text))
}

fn build_frontend_act_filtered(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for line in text.lines() {
        if should_keep_frontend_line(line) {
            out.push_str(line);
            out.push('\n');
        }
    }
    out
}

fn should_keep_frontend_line(line: &str) -> bool {
    let trimmed = line.trim_start();
    !(trimmed.starts_with("Warning: An update to ")
        && trimmed.contains("inside a test was not wrapped in act(...)"))
}
fn strip_core_lib_warnings(text: &str) -> Cow<'_, str> {
    if !text.lines().any(is_known_streaming_warning) {
        return Cow::Borrowed(text);
    }

    Cow::Owned(build_core_lib_filtered(text))
}

fn build_core_lib_filtered(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for line in text.lines() {
        if !is_known_streaming_warning(line) {
            out.push_str(line);
            out.push('\n');
        }
    }
    out
}

fn is_known_streaming_warning(line: &str) -> bool {
    let trimmed = line.trim_start();
    trimmed.starts_with("Warning: Large delta (")
        || trimmed.starts_with("Warning: Detected pattern of 3 large deltas for key")
        || trimmed.starts_with("Warning: Received MessageStart while state is Streaming.")
}

pub(crate) fn strip_allowed_generated_harness_large_stack_frames<'a>(
    check_name: &str,
    text: &'a str,
) -> (Cow<'a, str>, bool) {
    if !should_strip_generated_harness_warning(check_name, text) {
        return (Cow::Borrowed(text), false);
    }

    if has_real_source_span(text) {
        return (Cow::Borrowed(text), false);
    }

    (Cow::Owned(strip_generated_harness_block(text)), true)
}

fn should_strip_generated_harness_warning(check_name: &str, text: &str) -> bool {
    check_name == CLIPPY_CORE_CHECK_NAME
        && text.contains("error: this function may allocate ")
        && text.contains("could not compile `ralph-workflow` (lib test) due to 1 previous error")
}

fn has_real_source_span(text: &str) -> bool {
    text.lines().any(|line| {
        let trimmed = line.trim_start();
        trimmed.starts_with("-->")
            && trimmed.contains("ralph-workflow/src/")
            && !trimmed.contains("ralph-workflow/src/lib.rs:9:50")
    })
}
