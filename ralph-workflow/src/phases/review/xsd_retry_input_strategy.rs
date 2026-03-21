//! Pure domain logic for XSD retry input source selection.
//!
//! Determines the strategy for obtaining last output content for XSD retry prompts
//! without performing I/O. Boundary layer receives the decided strategy and executes it.

use std::path::{Path, PathBuf};

/// Strategy for obtaining XSD retry last-output content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum XsdRetryInputSource {
    /// Read from primary issues.xml file.
    Primary { path: PathBuf },
    /// Primary missing; read from archived .processed file.
    ArchivedFallback { path: PathBuf },
    /// Both files missing; use empty string as fallback.
    EmptyFallback,
}

/// Determine XSD retry input source based on file availability flags.
///
/// # Domain Policy
/// This function encodes the retry input fallback policy:
/// 1. Try primary issues.xml first
/// 2. If missing, try archived .processed file
/// 3. If both missing, use empty string
///
/// This is a pure decision function - boundary layer executes the I/O.
pub(crate) fn decide_xsd_retry_input_source(
    primary_exists: bool,
    archived_exists: bool,
    primary_path: &Path,
    archived_path: &Path,
) -> XsdRetryInputSource {
    if primary_exists {
        XsdRetryInputSource::Primary {
            path: primary_path.to_path_buf(),
        }
    } else if archived_exists {
        XsdRetryInputSource::ArchivedFallback {
            path: archived_path.to_path_buf(),
        }
    } else {
        XsdRetryInputSource::EmptyFallback
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefers_primary_when_both_exist() {
        let source = decide_xsd_retry_input_source(
            true,
            true,
            Path::new(".agent/tmp/issues.xml"),
            Path::new(".agent/tmp/issues.xml.processed"),
        );
        assert_eq!(
            source,
            XsdRetryInputSource::Primary {
                path: PathBuf::from(".agent/tmp/issues.xml")
            }
        );
    }

    #[test]
    fn uses_archived_when_primary_missing() {
        let source = decide_xsd_retry_input_source(
            false,
            true,
            Path::new(".agent/tmp/issues.xml"),
            Path::new(".agent/tmp/issues.xml.processed"),
        );
        assert_eq!(
            source,
            XsdRetryInputSource::ArchivedFallback {
                path: PathBuf::from(".agent/tmp/issues.xml.processed")
            }
        );
    }

    #[test]
    fn uses_empty_fallback_when_both_missing() {
        let source = decide_xsd_retry_input_source(
            false,
            false,
            Path::new(".agent/tmp/issues.xml"),
            Path::new(".agent/tmp/issues.xml.processed"),
        );
        assert_eq!(source, XsdRetryInputSource::EmptyFallback);
    }

    #[test]
    fn ignores_archived_when_primary_exists() {
        let source = decide_xsd_retry_input_source(
            true,
            false, // archived doesn't exist but shouldn't matter
            Path::new(".agent/tmp/issues.xml"),
            Path::new(".agent/tmp/issues.xml.processed"),
        );
        assert_eq!(
            source,
            XsdRetryInputSource::Primary {
                path: PathBuf::from(".agent/tmp/issues.xml")
            }
        );
    }
}
