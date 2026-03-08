// Basic rendering tests and comment stripping tests.

#[test]
fn test_render_template() {
    let template = Template::new("Hello {{NAME}}, your score is {{SCORE}}.");
    let variables = HashMap::from([("NAME", "Alice".to_string()), ("SCORE", "42".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello Alice, your score is 42.");
}

#[test]
fn test_missing_variable() {
    let template = Template::new("Hello {{NAME}}.");
    let variables = HashMap::new();
    let result = template.render(&variables);
    assert_eq!(
        result,
        Err(TemplateError::MissingVariable("NAME".to_string()))
    );
}

#[test]
fn test_no_variables() {
    let template = Template::new("Just plain text.");
    let rendered = template.render(&HashMap::new()).unwrap();
    assert_eq!(rendered, "Just plain text.");
}

#[test]
fn test_multiline_template() {
    let template = Template::new("Review this:\n{{DIFF}}\nEnd of review.");
    let variables = HashMap::from([("DIFF", "+ new line".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Review this:\n+ new line\nEnd of review.");
}

#[test]
fn test_whitespace_in_variables() {
    let template = Template::new("Value: {{ VALUE }}.");
    let variables = HashMap::from([("VALUE", "42".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Value: 42.");
}

#[test]
fn test_unclosed_opening_braces() {
    // Unclosed {{ should be ignored (no placeholder extracted)
    let template = Template::new("Hello {{NAME and some text");
    let rendered = template.render(&HashMap::new()).unwrap();
    // The unclosed braces are treated as literal text
    assert_eq!(rendered, "Hello {{NAME and some text");
}

#[test]
fn test_empty_variable_name() {
    // Empty variable name {{}} should be ignored (no placeholder extracted)
    let template = Template::new("Value: {{}}.");
    let rendered = template.render(&HashMap::new()).unwrap();
    // Empty placeholder is treated as literal text
    assert_eq!(rendered, "Value: {{}}.");
}

#[test]
fn test_whitespace_only_variable_name() {
    // Whitespace-only variable name {{   }} should be ignored
    let template = Template::new("Value: {{   }}.");
    let rendered = template.render(&HashMap::new()).unwrap();
    // Whitespace-only placeholder is treated as literal text
    assert_eq!(rendered, "Value: {{   }}.");
}

#[test]
fn test_multiple_unclosed_braces() {
    // Multiple unclosed {{ should all be ignored
    let template = Template::new("{{A text {{B text");
    let rendered = template.render(&HashMap::new()).unwrap();
    assert_eq!(rendered, "{{A text {{B text");
}

#[test]
fn test_partial_closing_brace() {
    // Single closing brace without the second should not close the placeholder
    let template = Template::new("Hello {{NAME}} and {{VAR}} text");
    let variables = HashMap::from([("NAME", "Alice".to_string()), ("VAR", "Bob".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello Alice and Bob text");
}

// =========================================================================
// Comment Stripping Tests
// =========================================================================

#[test]
fn test_inline_comment_stripped() {
    let template = Template::new("Hello {# this is a comment #}world.");
    let rendered = template.render(&HashMap::new()).unwrap();
    assert_eq!(rendered, "Hello world.");
}

#[test]
fn test_comment_on_own_line_stripped() {
    // Comment on its own line should be completely removed including the line itself
    let template = Template::new("Line 1\n{# This is a comment #}\nLine 2");
    let rendered = template.render(&HashMap::new()).unwrap();
    assert_eq!(rendered, "Line 1\nLine 2");
}

#[test]
fn test_multiline_comment() {
    // Multiline comments should be fully stripped
    let template = Template::new("Before{# comment\nspanning\nlines #}After");
    let rendered = template.render(&HashMap::new()).unwrap();
    assert_eq!(rendered, "BeforeAfter");
}

#[test]
fn test_comment_at_end_of_content_line() {
    // Comment at end of content line should only remove the comment
    let template = Template::new("Content{# comment #}\nMore");
    let rendered = template.render(&HashMap::new()).unwrap();
    assert_eq!(rendered, "Content\nMore");
}

#[test]
fn test_multiple_comments() {
    let template = Template::new("{# first #}A{# second #}B{# third #}");
    let rendered = template.render(&HashMap::new()).unwrap();
    assert_eq!(rendered, "AB");
}

#[test]
fn test_comment_with_variable() {
    // Comments should work alongside variables
    let template = Template::new("{# doc comment #}\nHello {{NAME}}!");
    let variables = HashMap::from([("NAME", "World".to_string())]);
    let rendered = template.render(&variables).unwrap();
    assert_eq!(rendered, "Hello World!");
}

#[test]
fn test_unclosed_comment_preserved() {
    // Unclosed comment should be treated as literal text
    let template = Template::new("Hello {# unclosed comment");
    let rendered = template.render(&HashMap::new()).unwrap();
    assert_eq!(rendered, "Hello {# unclosed comment");
}

#[test]
fn test_comment_documentation_use_case() {
    // Real use case: documentation comments in template
    let content = r"{# Template Version: 1.0 #}
{# This template generates commit messages #}
You are a commit message expert.

{# DIFF variable contains the git diff #}
DIFF:
{{DIFF}}

{# End of template #}
";
    let template = Template::new(content);
    let variables = HashMap::from([("DIFF", "+added line".to_string())]);
    let rendered = template.render(&variables).unwrap();

    // Verify documentation comments are stripped
    assert!(!rendered.contains("Template Version"));
    assert!(!rendered.contains("This template generates"));
    assert!(!rendered.contains("DIFF variable contains"));
    assert!(!rendered.contains("End of template"));

    // Verify content is preserved
    assert!(rendered.contains("You are a commit message expert."));
    assert!(rendered.contains("+added line"));
}
