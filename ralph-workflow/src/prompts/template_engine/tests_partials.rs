// Partials, conditional, default value, and loop tests.

// =========================================================================
// Partials Tests
// =========================================================================

#[test]
fn test_simple_partial_include() {
    let partials = HashMap::from([("header".to_string(), "Common Header".to_string())]);
    let template = Template::new("{{>header}}\nContent here");
    let variables = HashMap::new();
    let rendered = template
        .render_with_partials(&variables, &partials)
        .unwrap();
    assert_eq!(rendered, "Common Header\nContent here");
}

#[test]
fn test_partial_with_whitespace() {
    let partials = HashMap::from([("header".to_string(), "Header".to_string())]);
    let template = Template::new("{{> header}}\nContent");
    let variables = HashMap::new();
    let rendered = template
        .render_with_partials(&variables, &partials)
        .unwrap();
    assert_eq!(rendered, "Header\nContent");
}

#[test]
fn test_partial_with_variables() {
    let partials = HashMap::from([("greeting".to_string(), "Hello {{NAME}}\n".to_string())]);
    let template = Template::new("{{>greeting}}Body content");
    let variables = HashMap::from([("NAME", "World".to_string())]);
    let rendered = template
        .render_with_partials(&variables, &partials)
        .unwrap();
    assert_eq!(rendered, "Hello World\nBody content");
}

#[test]
fn test_multiple_partials() {
    let partials = HashMap::from([
        ("header".to_string(), "=== HEADER ===\n".to_string()),
        ("footer".to_string(), "\n=== FOOTER ===".to_string()),
    ]);
    let template = Template::new("{{>header}}Content{{>footer}}");
    let variables = HashMap::new();
    let rendered = template
        .render_with_partials(&variables, &partials)
        .unwrap();
    assert_eq!(rendered, "=== HEADER ===\nContent\n=== FOOTER ===");
}

#[test]
fn test_nested_partials() {
    let partials = HashMap::from([
        (
            "outer".to_string(),
            "Outer start\n{{>inner}}\nOuter end".to_string(),
        ),
        ("inner".to_string(), "INNER CONTENT".to_string()),
    ]);
    let template = Template::new("{{>outer}}");
    let variables = HashMap::new();
    let rendered = template
        .render_with_partials(&variables, &partials)
        .unwrap();
    assert_eq!(rendered, "Outer start\nINNER CONTENT\nOuter end");
}

#[test]
fn test_partial_not_found() {
    let partials = HashMap::new();
    let template = Template::new("{{>missing_partial}}");
    let variables = HashMap::new();
    let result = template.render_with_partials(&variables, &partials);
    assert_eq!(
        result,
        Err(TemplateError::PartialNotFound(
            "missing_partial".to_string()
        ))
    );
}

#[test]
fn test_circular_reference_detection() {
    let partials = HashMap::from([
        ("a".to_string(), "{{>b}}".to_string()),
        ("b".to_string(), "{{>a}}".to_string()),
    ]);
    let template = Template::new("{{>a}}");
    let variables = HashMap::new();
    let result = template.render_with_partials(&variables, &partials);
    match result {
        Err(TemplateError::CircularReference(chain)) => {
            // Chain should contain a circular reference between a and b
            assert_eq!(chain.len(), 3);
            assert!(chain.contains(&"a".to_string()));
            assert!(chain.contains(&"b".to_string()));
            // First and last elements should be the same (indicating a cycle)
            assert_eq!(chain.first(), chain.last());
        }
        _ => panic!("Expected CircularReference error"),
    }
}

#[test]
fn test_self_referential_partial() {
    let partials = HashMap::from([("loop".to_string(), "{{>loop}}".to_string())]);
    let template = Template::new("{{>loop}}");
    let variables = HashMap::new();
    let result = template.render_with_partials(&variables, &partials);
    match result {
        Err(TemplateError::CircularReference(chain)) => {
            assert_eq!(chain, vec!["loop".to_string(), "loop".to_string()]);
        }
        _ => panic!("Expected CircularReference error"),
    }
}

#[test]
fn test_partial_with_missing_variable() {
    let partials = HashMap::from([("greeting".to_string(), "Hello {{NAME}}".to_string())]);
    let template = Template::new("{{>greeting}}");
    let variables = HashMap::new(); // NAME not provided
    let result = template.render_with_partials(&variables, &partials);
    assert_eq!(
        result,
        Err(TemplateError::MissingVariable("NAME".to_string()))
    );
}

#[test]
fn test_partial_and_main_variables() {
    let partials = HashMap::from([("greeting".to_string(), "Hello {{NAME}}\n".to_string())]);
    let template = Template::new("{{>greeting}}Your score is {{SCORE}}");
    let variables = HashMap::from([("NAME", "Alice".to_string()), ("SCORE", "42".to_string())]);
    let rendered = template
        .render_with_partials(&variables, &partials)
        .unwrap();
    assert_eq!(rendered, "Hello Alice\nYour score is 42");
}

#[test]
fn test_partial_with_comments() {
    let partials = HashMap::from([(
        "header".to_string(),
        "{# This is a header #}Header Content\n".to_string(),
    )]);
    let template = Template::new("{{>header}}Body");
    let variables = HashMap::new();
    let rendered = template
        .render_with_partials(&variables, &partials)
        .unwrap();
    assert_eq!(rendered, "Header Content\nBody");
}

#[test]
fn test_partial_with_path_style_name() {
    let partials = HashMap::from([("shared/_header".to_string(), "Shared Header".to_string())]);
    let template = Template::new("{{> shared/_header}}\nContent");
    let variables = HashMap::new();
    let rendered = template
        .render_with_partials(&variables, &partials)
        .unwrap();
    assert_eq!(rendered, "Shared Header\nContent");
}

#[test]
fn test_backward_compatibility_render_without_partials() {
    // Ensure the original render() method still works
    let template = Template::new("Hello {{NAME}}");
    let variables = HashMap::from([("NAME", "World".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello World");
}

#[test]
fn test_empty_partial_name_ignored() {
    // {{> }} with empty name should be treated as literal text
    let template = Template::new("Before {{> }} After");
    let variables = HashMap::new();
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Before {{> }} After");
}

// =========================================================================
// Conditional Tests
// =========================================================================

#[test]
fn test_conditional_with_true_variable() {
    let template = Template::new("{% if NAME %}Hello {{NAME}}{% endif %}");
    let variables = HashMap::from([("NAME", "World".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello World");
}

#[test]
fn test_conditional_with_false_variable() {
    let template = Template::new("{% if NAME %}Hello {{NAME}}{% endif %}");
    let variables = HashMap::new(); // NAME not provided
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "");
}

#[test]
fn test_conditional_with_empty_variable() {
    let template = Template::new("{% if NAME %}Hello {{NAME}}{% endif %}");
    let variables = HashMap::from([("NAME", String::new())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "");
}

#[test]
fn test_conditional_with_negation_true() {
    let template = Template::new("{% if !NAME %}No name{% endif %}");
    let variables = HashMap::new(); // NAME not provided
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "No name");
}

#[test]
fn test_conditional_with_negation_false() {
    let template = Template::new("{% if !NAME %}No name{% endif %}");
    let variables = HashMap::from([("NAME", "Alice".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "");
}

#[test]
fn test_multiple_conditionals() {
    let template = Template::new(
        "{% if GREETING %}{{GREETING}}{% endif %} {% if NAME %}{{NAME}}{% endif %}",
    );
    let variables = HashMap::from([("NAME", "Bob".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, " Bob");
}

#[test]
fn test_conditional_with_surrounding_content() {
    let template = Template::new("Start {% if SHOW %}shown{% endif %} End");
    let variables = HashMap::from([("SHOW", "yes".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Start shown End");
}

// =========================================================================
// Default Value Tests
// =========================================================================

#[test]
fn test_default_value_with_missing_variable() {
    let template = Template::new("Hello {{NAME|default=\"Guest\"}}");
    let variables = HashMap::new();
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello Guest");
}

#[test]
fn test_default_value_with_empty_variable() {
    let template = Template::new("Hello {{NAME|default=\"Guest\"}}");
    let variables = HashMap::from([("NAME", String::new())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello Guest");
}

#[test]
fn test_default_value_with_present_variable() {
    let template = Template::new("Hello {{NAME|default=\"Guest\"}}");
    let variables = HashMap::from([("NAME", "Alice".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello Alice");
}

#[test]
fn test_default_value_with_single_quotes() {
    let template = Template::new("Hello {{NAME|default='Guest'}}");
    let variables = HashMap::new();
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello Guest");
}

// =========================================================================
// Loop Tests
// =========================================================================

#[test]
fn test_loop_with_items() {
    let template = Template::new("{% for item in ITEMS %}{{item}} {% endfor %}");
    let variables = HashMap::from([("ITEMS", "apple,banana,cherry".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "apple banana cherry ");
}

#[test]
fn test_loop_with_empty_list() {
    let template = Template::new("{% for item in ITEMS %}{{item}} {% endfor %}");
    let variables = HashMap::from([("ITEMS", String::new())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "");
}

#[test]
fn test_loop_with_missing_variable() {
    let template = Template::new("{% for item in ITEMS %}{{item}} {% endfor %}");
    let variables = HashMap::new();
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "");
}

#[test]
fn test_loop_with_conditional_inside() {
    let template =
        Template::new("{% for item in ITEMS %}{% if item %}{{item}} {% endif %}{% endfor %}");
    let variables = HashMap::from([("ITEMS", "apple,,cherry".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "apple cherry ");
}
