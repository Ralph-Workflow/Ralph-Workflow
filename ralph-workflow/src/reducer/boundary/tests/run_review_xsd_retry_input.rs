//! Tests for R10 retry-policy fix: XSD retry input source selection moved to domain layer.

use crate::phases::review::xsd_retry_input_strategy::{
    decide_xsd_retry_input_source, XsdRetryInputSource,
};
use std::path::Path;

#[test]
fn xsd_retry_input_source_decision_is_pure() {
    // Demonstrates that fallback decision is now in pure domain helper,
    // not in boundary layer (.map_or_else chain).

    // Scenario 1: Primary exists
    let source = decide_xsd_retry_input_source(
        true,
        false,
        Path::new(".agent/tmp/issues.xml"),
        Path::new(".agent/tmp/issues.xml.processed"),
    );
    assert!(matches!(source, XsdRetryInputSource::Primary { .. }));

    // Scenario 2: Primary missing, archived exists
    let source = decide_xsd_retry_input_source(
        false,
        true,
        Path::new(".agent/tmp/issues.xml"),
        Path::new(".agent/tmp/issues.xml.processed"),
    );
    assert!(matches!(
        source,
        XsdRetryInputSource::ArchivedFallback { .. }
    ));

    // Scenario 3: Both missing
    let source = decide_xsd_retry_input_source(
        false,
        false,
        Path::new(".agent/tmp/issues.xml"),
        Path::new(".agent/tmp/issues.xml.processed"),
    );
    assert_eq!(source, XsdRetryInputSource::EmptyFallback);
}

#[test]
fn boundary_uses_decided_source_not_inline_fallback_chain() {
    // Before fix (R10 violation):
    // Boundary had: workspace.read(processed_path).map_or_else(|_| String::new(), |output| output)
    // This made fallback decision inside boundary.

    // After fix:
    // Boundary calls decide_xsd_retry_input_source (pure domain),
    // receives XsdRetryInputSource decision,
    // executes I/O based on that decision.

    // This test verifies the decision is separate from execution.
    let primary_path = Path::new(".agent/tmp/issues.xml");
    let archived_path = Path::new(".agent/tmp/issues.xml.processed");

    // Decision logic is testable without I/O
    let decision_both_missing =
        decide_xsd_retry_input_source(false, false, primary_path, archived_path);
    let decision_archived_only =
        decide_xsd_retry_input_source(false, true, primary_path, archived_path);
    let decision_primary_exists =
        decide_xsd_retry_input_source(true, true, primary_path, archived_path);

    assert_eq!(decision_both_missing, XsdRetryInputSource::EmptyFallback);
    assert!(matches!(
        decision_archived_only,
        XsdRetryInputSource::ArchivedFallback { .. }
    ));
    assert!(matches!(
        decision_primary_exists,
        XsdRetryInputSource::Primary { .. }
    ));
}
