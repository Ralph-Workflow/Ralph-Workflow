//! Integration tests for commit message XML validation tolerant parsing.
//!
//! These tests verify tolerant parsing behavior through the public API boundary,
//! complementing the unit tests in the commit validation module.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All integration tests MUST follow the style guide defined in
//! **[`INTEGRATION_TESTS.md`](../INTEGRATION_TESTS.md)**.
//!
//! Before writing, modifying, or debugging any integration test, you MUST read
//! that document. Key principles:
//!
//! - Test **observable behavior**, not implementation details
//! - Mock only at **architectural boundaries** (filesystem, network, external APIs)
//! - Use `with_default_timeout()` wrapper for all tests
//! - NEVER use `cfg!(test)` branches in production code

use crate::test_timeout::with_default_timeout;

/// Test that commit message with unknown extra elements parses successfully.
///
/// This verifies that the tolerant parsing allows unknown XML elements
/// in the commit message (e.g., LLM reasoning tags).
#[test]
fn test_commit_xml_unknown_extra_elements_are_tolerated() {
    with_default_timeout(|| {
        let xml = r"<ralph-commit>
<ralph-subject>feat: add new feature</ralph-subject>
<llm-reasoning>I determined this is the best commit subject format</llm-reasoning>
<ralph-body>Implements the requested feature with tests.</ralph-body>
</ralph-commit>";

        let result = ralph_workflow::validate_xml_against_xsd(xml);
        assert!(
            result.is_ok(),
            "Commit with unknown elements should parse successfully: {:?}",
            result.err()
        );

        let elements = result.unwrap();
        assert_eq!(
            elements.subject, "feat: add new feature",
            "Should extract the subject correctly"
        );
        assert!(
            elements.body.is_some(),
            "Should extract the body even with unknown elements present"
        );
    });
}

/// Test that commit message with stray text between elements parses.
///
/// This verifies that non-essential text between XML elements
/// is ignored rather than causing a parse failure.
#[test]
fn test_commit_xml_stray_text_between_elements_is_tolerated() {
    with_default_timeout(|| {
        // Whitespace text between elements (already tolerated before this change)
        let xml = "<ralph-commit>\n\n<ralph-subject>fix(api): resolve null pointer</ralph-subject>\n\n</ralph-commit>";

        let result = ralph_workflow::validate_xml_against_xsd(xml);
        assert!(
            result.is_ok(),
            "Commit with whitespace text between elements should parse: {:?}",
            result.err()
        );

        let elements = result.unwrap();
        assert_eq!(elements.subject, "fix(api): resolve null pointer");
    });
}

/// Test that commit message missing required content still fails.
///
/// This verifies that tolerance only applies to non-essential elements,
/// and required elements (ralph-subject or ralph-skip) are still enforced.
#[test]
fn test_commit_xml_missing_required_content_still_fails() {
    with_default_timeout(|| {
        let xml = r"<ralph-commit>
<some-unknown-element>No subject provided</some-unknown-element>
</ralph-commit>";

        let result = ralph_workflow::validate_xml_against_xsd(xml);
        assert!(
            result.is_err(),
            "Commit missing required subject/skip should still fail"
        );
    });
}

/// Test that valid skip commit with unknown elements parses.
///
/// This verifies that unknown elements alongside ralph-skip are tolerated.
#[test]
fn test_commit_xml_skip_with_unknown_elements_is_tolerated() {
    with_default_timeout(|| {
        let xml = r"<ralph-commit>
<ralph-skip>No changes needed for this cycle</ralph-skip>
<llm-analysis>The codebase is already in the desired state</llm-analysis>
</ralph-commit>";

        let result = ralph_workflow::validate_xml_against_xsd(xml);
        assert!(
            result.is_ok(),
            "Skip commit with unknown elements should parse: {:?}",
            result.err()
        );

        let elements = result.unwrap();
        assert!(
            elements.skip_reason.is_some(),
            "Skip reason should be extracted"
        );
    });
}

/// Test that mutual exclusivity between subject and skip is still enforced.
///
/// This verifies that tolerant parsing does not affect the semantic constraint
/// that ralph-subject and ralph-skip cannot coexist.
#[test]
fn test_commit_xml_mutual_exclusivity_still_enforced() {
    with_default_timeout(|| {
        let xml = r"<ralph-commit>
<ralph-skip>No changes</ralph-skip>
<ralph-subject>feat: should not be here</ralph-subject>
</ralph-commit>";

        let result = ralph_workflow::validate_xml_against_xsd(xml);
        assert!(
            result.is_err(),
            "Mixed skip and commit elements should still be rejected"
        );
    });
}

/// Test that truly malformed XML is still rejected.
///
/// This verifies that the tolerance only applies to structural variations
/// in well-formed XML, not to malformed XML.
#[test]
fn test_commit_xml_truly_malformed_xml_still_rejected() {
    with_default_timeout(|| {
        let xml = "<ralph-commit><ralph-subject>feat: unclosed</ralph-commit>";

        let result = ralph_workflow::validate_xml_against_xsd(xml);
        assert!(
            result.is_err(),
            "Truly malformed XML (unclosed tag) should still be rejected"
        );
    });
}
