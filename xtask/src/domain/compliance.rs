//! Pure domain helpers for the compliance checks.
//!
//! These helpers decide what message/status to emit after the boundary layer
//! has already gathered the raw data (file lists, violation traces, errors).

const STATUS_PASS: u8 = 0;
const STATUS_WARNING: u8 = 1;
const STATUS_ERROR: u8 = 2;

/// Summary produced by the domain layer for a compliance check.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ComplianceSummary {
    pub status_code: u8,
    pub message: String,
}

impl ComplianceSummary {
    fn new(status_code: u8, message: String) -> Self {
        Self {
            status_code,
            message,
        }
    }
}

pub fn shell_script_scan_result(found: &[String], walk_errors: &[String]) -> ComplianceSummary {
    if !walk_errors.is_empty() {
        return ComplianceSummary::new(
            STATUS_ERROR,
            format!(
                "Failed to scan for .sh files due to directory walk errors:\n{}",
                walk_errors.join("\n")
            ),
        );
    }

    if found.is_empty() {
        ComplianceSummary::new(STATUS_PASS, String::new())
    } else {
        ComplianceSummary::new(
            STATUS_ERROR,
            format!(
                "Found {} .sh file(s) that must not exist after the shell-script migration:\n{}",
                found.len(),
                found.join("\n")
            ),
        )
    }
}

pub fn timeout_wrapper_scan_result(
    violations: &[String],
    read_errors: &[String],
) -> ComplianceSummary {
    if !read_errors.is_empty() {
        return ComplianceSummary::new(
            STATUS_ERROR,
            format!(
                "Failed to read {} integration test file(s) during timeout-wrapper compliance scan:\n{}",
                read_errors.len(),
                read_errors.join("\n")
            ),
        );
    }

    if violations.is_empty() {
        ComplianceSummary::new(STATUS_PASS, String::new())
    } else {
        ComplianceSummary::new(
            STATUS_WARNING,
            format!(
                "Found {} test(s) missing timeout wrapper:\n{}",
                violations.len(),
                violations.join("\n")
            ),
        )
    }
}

pub fn tailwind_scan_result(violations: &[String], read_errors: &[String]) -> ComplianceSummary {
    if !read_errors.is_empty() {
        return ComplianceSummary::new(
            STATUS_ERROR,
            format!(
                "Failed to read {} Angular template file(s) during Tailwind 4 migration scan:\n{}",
                read_errors.len(),
                read_errors.join("\n")
            ),
        );
    }

    if violations.is_empty() {
        ComplianceSummary::new(STATUS_PASS, String::new())
    } else {
        ComplianceSummary::new(
            STATUS_WARNING,
            format!(
                "Found {} Tailwind 3-only class usage(s) in Angular templates that do not exist in Tailwind 4. Each affected component/file needs rework:\n{}",
                violations.len(),
                violations.join("\n")
            ),
        )
    }
}
