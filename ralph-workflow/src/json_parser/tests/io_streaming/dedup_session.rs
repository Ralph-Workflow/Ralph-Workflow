// Tests for `StreamingSession` deduplication behavior (prefix trie).

/// Test that `StreamingSession`'s `is_content_rendered` works correctly with prefix trie.
#[cfg(test)]
#[test]
fn test_streaming_session_is_content_rendered() {
    use crate::json_parser::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // First delta - should not skip (not rendered yet)
    session.on_text_delta(0, "Hello");
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "First delta should not be detected as rendered"
    );

    // Mark as rendered using trie
    session.mark_content_rendered(super::types::ContentType::Text, "0");

    // Now should skip (exact match in trie)
    assert!(
        session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Same content should be detected as already rendered"
    );

    // Second delta that changes content - should not skip (new content)
    session.on_text_delta(0, " World");
    // "Hello World" is not an exact match for "Hello" in trie
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Changed content should not be detected as rendered"
    );

    // But it should detect prefix match
    assert!(
        session.has_rendered_prefix(super::types::ContentType::Text, "0"),
        "Changed content should have prefix match (starts with 'Hello')"
    );

    // Mark new content as rendered
    session.mark_content_rendered(super::types::ContentType::Text, "0");

    // Now exact match should work
    assert!(
        session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Marked content should be detected as rendered"
    );
}

/// Test that `mark_content_rendered` updates the prefix trie correctly.
#[cfg(test)]
#[test]
fn test_streaming_session_mark_content_rendered() {
    use crate::json_parser::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // First delta
    session.on_text_delta(0, "Hello");

    // Initially, should not skip (nothing in trie yet)
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should not skip before first render"
    );

    // Mark as rendered using trie
    session.mark_content_rendered(super::types::ContentType::Text, "0");

    // Now should skip (exact match in trie)
    assert!(
        session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should skip after marking same content as rendered"
    );

    // Add more content
    session.on_text_delta(0, " World");

    // Should not skip anymore (content is different - "Hello World" != "Hello")
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should not skip after content changes"
    );

    // But prefix match should detect that "Hello World" starts with "Hello"
    assert!(
        session.has_rendered_prefix(super::types::ContentType::Text, "0"),
        "Should detect prefix match (Hello World starts with Hello)"
    );
}

/// Test that `message_start` clears the rendered content trie.
#[cfg(test)]
#[test]
fn test_message_start_clears_rendered_content() {
    use crate::json_parser::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // First delta and mark as rendered
    session.on_text_delta(0, "Hello");
    session.mark_content_rendered(super::types::ContentType::Text, "0");

    // Should skip (exact match in trie)
    assert!(
        session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should detect rendered content before message_start"
    );

    // New message - should clear trie
    session.on_message_start();

    // Add same content again
    session.on_text_delta(0, "Hello");

    // Should not skip (trie was cleared)
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should not skip after message_start clears trie"
    );
}
