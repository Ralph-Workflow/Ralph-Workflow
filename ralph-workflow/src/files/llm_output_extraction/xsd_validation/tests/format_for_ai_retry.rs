// ============================================================================
// Tests for format_for_ai_retry()
//
// Tests verify the four-section dumb-agent-proof format:
// - What failed: one sentence plain language
// - Where it failed: exact element/path/tag
// - How to fix: ordered checklist
// - Do not do: anti-actions list
// ============================================================================

#[test]
fn test_format_for_ai_retry_missing_required_element() {
    let error = XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-subject".to_string(),
        expected: "<ralph-subject> element (required)".to_string(),
        found: "no <ralph-subject> found".to_string(),
        suggestion: "Add <ralph-subject>type(scope): description</ralph-subject>".to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();
    // New format uses "What failed:" style
    assert!(formatted.contains("What failed"));
    assert!(formatted.contains("ralph-subject"));
    assert!(formatted.contains("Add the missing"));
    // Should have all four sections
    assert!(formatted.contains("Where it failed"));
    assert!(formatted.contains("How to fix"));
    assert!(formatted.contains("Do not do"));
}

#[test]
fn test_format_for_ai_retry_with_example() {
    let error = XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-subject".to_string(),
        expected: "<ralph-subject> element (required)".to_string(),
        found: "no <ralph-subject> found".to_string(),
        suggestion: "Add the required element".to_string(),
        example: Some(
            "<ralph-commit><ralph-subject>feat: example</ralph-subject></ralph-commit>".into(),
        ),
    };

    let formatted = error.format_for_ai_retry();
    assert!(formatted.contains("Example of correct format:"));
    assert!(formatted.contains("feat: example"));
    // Should have all four sections
    assert!(formatted.contains("What failed"));
    assert!(formatted.contains("Where it failed"));
    assert!(formatted.contains("How to fix"));
    assert!(formatted.contains("Do not do"));
}

#[test]
fn test_format_for_ai_retry_unexpected_element() {
    let error = XsdValidationError {
        error_type: XsdErrorType::UnexpectedElement,
        element_path: "<unknown-tag>".to_string(),
        expected: "only valid commit message tags".to_string(),
        found: "unexpected tag: <unknown-tag>".to_string(),
        suggestion: "Remove the <unknown-tag> tag".to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();
    // New format uses "What failed:" style
    assert!(formatted.contains("What failed"));
    assert!(formatted.contains("<unknown-tag>"));
    // Should have all four sections
    assert!(formatted.contains("Where it failed"));
    assert!(formatted.contains("How to fix"));
    assert!(formatted.contains("Do not do"));
}

#[test]
fn test_format_for_ai_retry_invalid_content() {
    let error = XsdValidationError {
        error_type: XsdErrorType::InvalidContent,
        element_path: "ralph-subject".to_string(),
        expected: "conventional commit format".to_string(),
        found: "bad subject".to_string(),
        suggestion: "Use conventional commit format".to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();
    // New format uses "What failed:" style
    assert!(formatted.contains("What failed"));
    assert!(formatted.contains("ralph-subject"));
    // Should have all four sections
    assert!(formatted.contains("Where it failed"));
    assert!(formatted.contains("How to fix"));
    assert!(formatted.contains("Do not do"));
}

#[test]
fn test_format_for_ai_retry_malformed_xml() {
    let error = XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: "xml".to_string(),
        expected: "valid XML declaration ending with ?>".to_string(),
        found: "unclosed XML declaration".to_string(),
        suggestion: "Ensure XML declaration is properly closed".to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();
    // New format emphasizes malformed XML as primary issue
    assert!(formatted.contains("What failed"));
    assert!(formatted.contains("malformed"));
    assert!(formatted.contains("XML"));
    // Should have all four sections
    assert!(formatted.contains("Where it failed"));
    assert!(formatted.contains("How to fix"));
    assert!(formatted.contains("Do not do"));
}

#[test]
fn test_format_for_ai_retry_illegal_character_emphasis() {
    // Create an error that represents an illegal character (NUL byte)
    let error = XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: "xml".to_string(),
        expected: "valid XML 1.0 content (no illegal control characters)".to_string(),
        found: "illegal character NUL (null byte) at byte position 42".to_string(),
        suggestion: "NUL byte found at position 42. Common causes:\n\
                         - Intended to use non-breaking space (\\u00A0) but wrote \\u0000 instead\n\
                         Near: ...git\0diff..."
            .to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();

    // Verify the formatted output emphasizes illegal character issue
    assert!(
        formatted.contains("illegal character"),
        "Should emphasize illegal character issue"
    );
    assert!(
        formatted.contains("NUL"),
        "Should identify the specific character"
    );
    assert!(
        formatted.contains("\\u00A0") || formatted.contains("non-breaking space"),
        "Should mention common NBSP typo"
    );
    // Should have all four sections
    assert!(formatted.contains("What failed"));
    assert!(formatted.contains("Where it failed"));
    assert!(formatted.contains("How to fix"));
    assert!(formatted.contains("Do not do"));
}

#[test]
fn test_format_for_ai_retry_illegal_character_includes_fix_marker() {
    let error = XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: "xml".to_string(),
        expected: "valid XML 1.0 content (no illegal control characters)".to_string(),
        found: "illegal character NUL (null byte) at byte position 42".to_string(),
        suggestion: "NUL byte found at position 42. Common causes:\n\
                         - Intended to use non-breaking space (\\u00A0) but wrote \\u0000 instead\n\
                         Near: ...git\0diff..."
            .to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();

    assert!(
        formatted.contains("How to fix"),
        "Illegal character errors should include a fix section, got:\n{formatted}"
    );
}

#[test]
fn test_format_for_ai_retry_generic_malformed_xml() {
    // Create a generic malformed XML error (not illegal character)
    let error = XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: "ralph-issues".to_string(),
        expected: "well-formed XML".to_string(),
        found: "parse error: unclosed tag".to_string(),
        suggestion: "Ensure all tags are properly closed".to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();

    // Verify this uses standard formatting with malformed XML emphasis
    assert!(
        formatted.contains("malformed"),
        "Should emphasize malformed XML"
    );
    assert!(
        !formatted.contains("illegal character"),
        "Should NOT use illegal character emphasis for generic errors"
    );
    // Should have all four sections
    assert!(formatted.contains("What failed"));
    assert!(formatted.contains("Where it failed"));
    assert!(formatted.contains("How to fix"));
    assert!(formatted.contains("Do not do"));
}

// ============================================================================
// Regression Tests for Dumb-Agent-Proof Contract
// ============================================================================

/// Regression test: format_for_ai_retry MUST produce four-section structure.
/// This ensures retry prompts can guide weak agents deterministically.
#[test]
fn test_format_for_ai_retry_has_four_sections() {
    let error = XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-subject".to_string(),
        expected: "<ralph-subject> element (required)".to_string(),
        found: "no <ralph-subject> found".to_string(),
        suggestion: "Add <ralph-subject>type: description</ralph-subject>".to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();

    // Must have What failed section
    assert!(
        formatted.contains("What failed"),
        "Should have 'What failed' section. Got:\n{formatted}"
    );

    // Must have Where it failed section
    assert!(
        formatted.contains("Where it failed"),
        "Should have 'Where it failed' section. Got:\n{formatted}"
    );

    // Must have How to fix section
    assert!(
        formatted.contains("How to fix"),
        "Should have 'How to fix' section. Got:\n{formatted}"
    );

    // Must have Do not do section (anti-actions)
    assert!(
        formatted.contains("Do not do"),
        "Should have 'Do not do' anti-actions section. Got:\n{formatted}"
    );
}

/// Regression test: malformed XML MUST be prioritized as primary correction target.
#[test]
fn test_format_for_ai_retry_malformed_xml_is_primary_target() {
    let error = XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: "xml".to_string(),
        expected: "valid XML".to_string(),
        found: "XML parse error".to_string(),
        suggestion: "Fix XML structure first".to_string(),
        example: None,
    };

    let formatted = error.format_for_ai_retry();

    // Malformed XML should be mentioned prominently as primary issue
    assert!(
        formatted.contains("malformed"),
        "Malformed XML should be primary emphasis. Got:\n{formatted}"
    );
}

/// Regression test: format_for_ai_retry MUST NOT embed prior prompt dumps.
#[test]
fn test_format_for_ai_retry_no_prior_prompt_dump() {
    let error = XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-subject".to_string(),
        expected: "<ralph-subject> element".to_string(),
        found: "not found".to_string(),
        suggestion: "Add the element".to_string(),
        example: Some("<ralph-commit><ralph-subject>fix: bug</ralph-subject></ralph-commit>".into()),
    };

    let formatted = error.format_for_ai_retry();

    // The example should appear but not as a large embedded dump
    assert!(
        !formatted.contains("PRIOR PROMPT"),
        "Should not contain prior PROMPT content. Got:\n{formatted}"
    );
    // Note: The word "plan" in the example is fine - the test checks that we don't
    // embed large prior prompt blocks, not that we avoid all mention of planning
}