// TDD RED tests for CheckpointLoadError typed variants.
// These tests verify load_checkpoint_with_fallback returns typed errors, not Box<dyn Error>.

#[test]
fn test_load_fallback_invalid_json_returns_invalid_json_variant() {
    let result = super::super::load_checkpoint_with_fallback("not json at all");
    assert!(
        matches!(result, Err(super::super::CheckpointLoadError::InvalidJson(_))),
        "expected InvalidJson, got {result:?}"
    );
}

#[test]
fn test_load_fallback_missing_version_returns_missing_version_variant() {
    let result = super::super::load_checkpoint_with_fallback(r#"{"phase": "Develop"}"#);
    assert!(
        matches!(result, Err(super::super::CheckpointLoadError::MissingVersion)),
        "expected MissingVersion, got {result:?}"
    );
}

#[test]
fn test_load_fallback_future_version_returns_unsupported_too_new_variant() {
    let result = super::super::load_checkpoint_with_fallback(r#"{"version": 99}"#);
    assert!(
        matches!(result, Err(super::super::CheckpointLoadError::UnsupportedVersionTooNew(99))),
        "expected UnsupportedVersionTooNew(99), got {result:?}"
    );
}

#[test]
fn test_load_fallback_legacy_version_returns_legacy_version_variant() {
    let result = super::super::load_checkpoint_with_fallback(r#"{"version": 1}"#);
    assert!(
        matches!(result, Err(super::super::CheckpointLoadError::LegacyVersion(1))),
        "expected LegacyVersion(1), got {result:?}"
    );
}

#[test]
fn test_checkpoint_load_error_display_contains_version_number() {
    let err = super::super::CheckpointLoadError::UnsupportedVersionTooNew(42);
    assert!(err.to_string().contains("42"), "display should mention version 42");
}

#[test]
fn test_checkpoint_load_error_legacy_display_contains_version() {
    let err = super::super::CheckpointLoadError::LegacyVersion(1);
    assert!(err.to_string().contains("1"), "display should mention version 1");
}

#[test]
fn test_checkpoint_load_error_missing_version_display() {
    let err = super::super::CheckpointLoadError::MissingVersion;
    assert!(!err.to_string().is_empty());
}
