#[test]
fn merge_delta_tracks_first_delta_and_accumulates_content() {
    let mut accumulated = std::collections::HashMap::new();
    let mut key_order = Vec::new();
    let mut output_started_for_key = std::collections::HashSet::new();

    let first = merge_delta(
        &mut accumulated,
        &mut key_order,
        &mut output_started_for_key,
        ContentType::Thinking,
        "reasoning",
        "Hello",
    );
    let second = merge_delta(
        &mut accumulated,
        &mut key_order,
        &mut output_started_for_key,
        ContentType::Thinking,
        "reasoning",
        " World",
    );

    assert!(first);
    assert!(!second);
    assert_eq!(
        accumulated.get(&(ContentType::Thinking, "reasoning".to_string())),
        Some(&"Hello World".to_string())
    );
    assert_eq!(
        key_order,
        vec![(ContentType::Thinking, "reasoning".to_string())]
    );
}

#[test]
fn sorted_content_keys_orders_numeric_then_lexical() {
    let mut accumulated = std::collections::HashMap::new();
    accumulated.insert((ContentType::Text, "10".to_string()), "a".to_string());
    accumulated.insert((ContentType::Text, "2".to_string()), "b".to_string());
    accumulated.insert((ContentType::Text, "alpha".to_string()), "c".to_string());
    accumulated.insert(
        (ContentType::Thinking, "0".to_string()),
        "ignored".to_string(),
    );

    let keys = sorted_content_keys(&accumulated, ContentType::Text);

    assert_eq!(
        keys,
        vec!["2".to_string(), "10".to_string(), "alpha".to_string()]
    );
}

#[test]
fn build_tool_use_reconstruction_prefers_hints_then_session_names() {
    let mut accumulated = std::collections::HashMap::new();
    accumulated.insert((ContentType::ToolInput, "0".to_string()), "{}".to_string());
    accumulated.insert(
        (ContentType::ToolInput, "1".to_string()),
        "{\"x\":1}".to_string(),
    );

    let mut tool_names = std::collections::HashMap::new();
    tool_names.insert(1_u64, Some("fallback".to_string()));

    let mut hints = std::collections::HashMap::new();
    hints.insert(0_usize, "hinted".to_string());

    let reconstructed = build_tool_use_reconstruction(&accumulated, &tool_names, Some(&hints));

    assert_eq!(
        reconstructed,
        "TOOL_USE:hinted:{}TOOL_USE:fallback:{\"x\":1}"
    );
}

#[test]
fn compute_hash_is_stable_for_same_input() {
    assert_eq!(compute_hash("same"), compute_hash("same"));
}

#[test]
fn compute_content_hash_from_accumulated_is_order_independent() {
    let mut first = std::collections::HashMap::new();
    first.insert((ContentType::Text, "1".to_string()), "alpha".to_string());
    first.insert((ContentType::ToolInput, "2".to_string()), "{}".to_string());

    let mut second = std::collections::HashMap::new();
    second.insert((ContentType::ToolInput, "2".to_string()), "{}".to_string());
    second.insert((ContentType::Text, "1".to_string()), "alpha".to_string());

    assert_eq!(
        compute_content_hash_from_accumulated(&first),
        compute_content_hash_from_accumulated(&second)
    );
}

#[test]
fn is_duplicate_text_content_matches_concatenated_text_stream() {
    let mut accumulated = std::collections::HashMap::new();
    accumulated.insert((ContentType::Text, "0".to_string()), "Hello".to_string());
    accumulated.insert((ContentType::Text, "1".to_string()), " World".to_string());

    assert!(is_duplicate_text_content(&accumulated, "Hello World"));
    assert!(!is_duplicate_text_content(&accumulated, "Hello there"));
}

#[test]
fn build_mixed_content_reconstruction_orders_text_then_tool_use() {
    let mut accumulated = std::collections::HashMap::new();
    accumulated.insert((ContentType::Text, "0".to_string()), "Answer ".to_string());
    accumulated.insert((ContentType::ToolInput, "0".to_string()), "{}".to_string());
    accumulated.insert(
        (ContentType::Thinking, "0".to_string()),
        "hidden".to_string(),
    );

    let mut tool_names = std::collections::HashMap::new();
    tool_names.insert(0_u64, Some("lookup".to_string()));

    let reconstructed = build_mixed_content_reconstruction(&accumulated, &tool_names, None);

    assert_eq!(reconstructed, "Answer TOOL_USE:lookup:{}");
}

#[test]
fn snapshot_helpers_detect_and_extract_new_suffix() {
    let previous = "0123456789012345678901234567890123456789";
    let snapshot = "0123456789012345678901234567890123456789-new";

    let mut accumulated = std::collections::HashMap::new();
    accumulated.insert(
        (ContentType::Text, "main".to_string()),
        previous.to_string(),
    );

    assert!(is_likely_snapshot(&accumulated, snapshot, "main"));
    let delta_start = match extract_delta_from_snapshot(&accumulated, snapshot, "main") {
        Ok(index) => index,
        Err(error) => panic!("snapshot should produce suffix index: {error}"),
    };
    assert_eq!(&snapshot[delta_start..], "-new");
}
