// Substitution log tests.

// =========================================================================
// Substitution Log Tests
// =========================================================================

#[test]
fn test_substitution_log_value_provided() {
    let template = Template::new("Hello {{NAME}}");
    let variables = HashMap::from([("NAME", "Alice".to_string())]);

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    assert_eq!(rendered.content, "Hello Alice");
    assert_eq!(rendered.log.template_name, "test");
    assert_eq!(rendered.log.substituted.len(), 1);
    assert_eq!(rendered.log.substituted[0].name, "NAME");
    assert_eq!(
        rendered.log.substituted[0].source,
        crate::prompts::SubstitutionSource::Value
    );
    assert!(rendered.log.is_complete());
    assert!(rendered.log.unsubstituted.is_empty());
}

#[test]
fn test_substitution_log_default_used() {
    let template = Template::new("Hello {{NAME|default=\"Guest\"}}");
    let variables = HashMap::new();

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    assert_eq!(rendered.content, "Hello Guest");
    assert_eq!(rendered.log.substituted.len(), 1);
    assert_eq!(rendered.log.substituted[0].name, "NAME");
    assert_eq!(
        rendered.log.substituted[0].source,
        crate::prompts::SubstitutionSource::Default
    );
    assert!(rendered.log.is_complete());
}

#[test]
fn test_substitution_log_empty_with_default() {
    let template = Template::new("Hello {{NAME|default=\"Guest\"}}");
    let variables = HashMap::from([("NAME", String::new())]);

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    assert_eq!(rendered.content, "Hello Guest");
    assert_eq!(
        rendered.log.substituted[0].source,
        crate::prompts::SubstitutionSource::EmptyWithDefault
    );
    assert!(rendered.log.is_complete());
}

#[test]
fn test_substitution_log_truly_missing() {
    let template = Template::new("Hello {{NAME}}");
    let variables = HashMap::new();

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .expect("render_with_log should succeed even when variables are missing");

    assert_eq!(rendered.content, "Hello {{NAME}}");
    assert!(rendered.log.substituted.is_empty());
    assert_eq!(rendered.log.unsubstituted, vec!["NAME".to_string()]);
    assert!(!rendered.log.is_complete());
}

#[test]
fn test_substitution_log_empty_without_default_is_unsubstituted() {
    let template = Template::new("Hello {{NAME}}");
    let variables = HashMap::from([("NAME", String::new())]);

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    assert_eq!(rendered.content, "Hello {{NAME}}");
    assert!(rendered.log.substituted.is_empty());
    assert_eq!(rendered.log.unsubstituted, vec!["NAME".to_string()]);
    assert!(!rendered.log.is_complete());
}

#[test]
fn test_substitution_log_jsx_in_value() {
    let template = Template::new("Code: {{CODE}}");
    let variables = HashMap::from([("CODE", "style={{ zIndex: 0 }}".to_string())]);

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    assert!(rendered.content.contains("{{ zIndex: 0 }}"));
    assert_eq!(
        rendered.log.substituted[0].source,
        crate::prompts::SubstitutionSource::Value
    );
    assert!(rendered.log.is_complete());
}

#[test]
fn test_substitution_log_merges_loop_substitutions() {
    let template =
        Template::new("{% for item in ITEMS %}{{item}} {{MISSING}}{% endfor %}");
    let variables = HashMap::from([("ITEMS", "a,b".to_string())]);

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    assert!(rendered.content.contains('a'));
    assert!(rendered.content.contains('b'));
    assert!(
        rendered
            .log
            .substituted
            .iter()
            .any(|entry| entry.name == "item")
    );
}

#[test]
fn test_substitution_log_loop_value_with_braces() {
    let template = Template::new("{% for item in ITEMS %}Item: {{item}}{% endfor %}");
    let variables = HashMap::from([("ITEMS", "style={{ zIndex: 0 }}".to_string())]);

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    assert!(rendered.content.contains("{{ zIndex: 0 }}"));
    assert!(
        rendered
            .log
            .substituted
            .iter()
            .any(|entry| entry.name == "item")
    );
    assert!(rendered.log.unsubstituted.is_empty());
    assert!(rendered.log.is_complete());
}

#[test]
fn test_substitution_log_merges_partial_substitutions() {
    let partials = HashMap::from([("greeting".to_string(), "Hello {{NAME}}".to_string())]);
    let template = Template::new("{{>greeting}}");
    let variables = HashMap::from([("NAME", "World".to_string())]);

    let rendered = template
        .render_with_log("test", &variables, &partials)
        .unwrap();

    assert_eq!(rendered.content, "Hello World");
    assert!(
        rendered
            .log
            .substituted
            .iter()
            .any(|entry| entry.name == "NAME")
    );
}

#[test]
fn test_defaults_used_helper() {
    let template = Template::new("{{A}} {{B|default=\"x\"}} {{C|default=\"y\"}}");
    let variables = HashMap::from([("A", "a".to_string())]);

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    let defaults = rendered.log.defaults_used();
    assert_eq!(defaults.len(), 2);
    assert!(defaults.contains(&"B"));
    assert!(defaults.contains(&"C"));
    assert!(!defaults.contains(&"A"));
}

#[test]
fn test_substitution_log_mixed() {
    let template = Template::new("{{A}} {{B|default=\"b\"}} {{C}}");
    let variables = HashMap::from([("A", "a".to_string()), ("C", "c".to_string())]);
    // B will use default

    let rendered = template
        .render_with_log("test", &variables, &HashMap::new())
        .unwrap();

    assert_eq!(rendered.content, "a b c");
    assert_eq!(rendered.log.substituted.len(), 3);
    assert!(rendered.log.is_complete());

    // Check specific sources
    let a_entry = rendered
        .log
        .substituted
        .iter()
        .find(|e| e.name == "A")
        .unwrap();
    assert_eq!(a_entry.source, crate::prompts::SubstitutionSource::Value);

    let b_entry = rendered
        .log
        .substituted
        .iter()
        .find(|e| e.name == "B")
        .unwrap();
    assert_eq!(b_entry.source, crate::prompts::SubstitutionSource::Default);

    let c_entry = rendered
        .log
        .substituted
        .iter()
        .find(|e| e.name == "C")
        .unwrap();
    assert_eq!(c_entry.source, crate::prompts::SubstitutionSource::Value);
}
