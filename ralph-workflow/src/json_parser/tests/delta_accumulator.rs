// Tests for DeltaAccumulator (shared type).
//
// Tests for the delta accumulation functionality used across parsers.

#[test]
fn test_delta_accumulator_text() {
    let acc = super::types::DeltaAccumulator::new()
        .add_delta(super::types::ContentType::Text, "0", "Hello, ")
        .add_delta(super::types::ContentType::Text, "0", "World!");

    assert_eq!(
        acc.get(super::types::ContentType::Text, "0"),
        Some("Hello, World!")
    );
    assert!(!acc.is_empty());
}

#[test]
fn test_delta_accumulator_thinking() {
    let acc = super::types::DeltaAccumulator::new()
        .add_delta(super::types::ContentType::Thinking, "0", "Let me think...")
        .add_delta(super::types::ContentType::Thinking, "0", " Done.");

    assert_eq!(
        acc.get(super::types::ContentType::Thinking, "0"),
        Some("Let me think... Done.")
    );
}

#[test]
fn test_delta_accumulator_generic() {
    let acc = super::types::DeltaAccumulator::new()
        .add_delta(super::types::ContentType::Text, "custom_key", "Alpha ")
        .add_delta(super::types::ContentType::Text, "custom_key", "Beta");

    assert_eq!(
        acc.get(super::types::ContentType::Text, "custom_key"),
        Some("Alpha Beta")
    );
}

#[test]
fn test_delta_accumulator_clear() {
    let acc = super::types::DeltaAccumulator::new().add_delta(
        super::types::ContentType::Text,
        "0",
        "Some text",
    );
    assert!(!acc.is_empty());

    let acc = acc.clear();
    assert!(acc.is_empty());
    assert_eq!(acc.get(super::types::ContentType::Text, "0"), None);
}

#[test]
fn test_delta_accumulator_clear_key() {
    let acc = super::types::DeltaAccumulator::new()
        .add_delta(super::types::ContentType::Text, "0", "Text 0")
        .add_delta(super::types::ContentType::Text, "1", "Text 1");

    let acc = acc.clear_key(super::types::ContentType::Text, "0");
    assert_eq!(acc.get(super::types::ContentType::Text, "0"), None);
    assert_eq!(
        acc.get(super::types::ContentType::Text, "1"),
        Some("Text 1")
    );
}
