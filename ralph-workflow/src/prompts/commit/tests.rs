use super::*;
use crate::agents::session::SessionDrain;
use crate::prompts::SessionCapabilities;
use crate::workspace::MemoryWorkspace;
use regex::Regex;

#[test]
fn test_prompt_fix() {
    let result = prompt_fix(
        "test prompt content",
        "test plan content",
        "test issues content",
    );
    assert!(result.contains("test issues content"));
    // Agent should NOT modify the ISSUES content - it is provided for reference only
    assert!(result.contains("MUST NOT modify the ISSUES content"));
    assert!(result.contains("provided for reference only"));
    // Fix prompt should encourage fixing root cause (incl. necessary refactors)
    assert!(
        result.contains("getting rid of tech debt is necessary to fix a bug"),
        "Fix prompt should instruct agent to do necessary refactors"
    );
    assert!(result.contains("FIX MODE"));
    // Agent should return status as XML output
    assert!(result.contains("<ralph-fix-result>"));
    assert!(result.contains("<ralph-status>"));
    assert!(result.contains("all_issues_addressed"));
    assert!(result.contains("issues_remain"));
    // Should include PROMPT and PLAN context
    assert!(result.contains("test prompt content"));
    assert!(result.contains("test plan content"));

    // Shared partials should be expanded
    assert!(
        result.contains("*** UNATTENDED MODE - NO USER INTERACTION ***"),
        "fix_mode_xml should render shared/_unattended_mode partial"
    );
    assert!(
        !result.contains("{{>"),
        "fix_mode_xml should not contain raw partial directives"
    );
}

#[test]
fn test_prompt_fix_with_empty_context() {
    let result = prompt_fix("", "", "");
    assert!(result.contains("FIX MODE"));
    // Should still render successfully with empty context
    assert!(!result.is_empty());
}

#[test]
fn test_notes_md_references_are_minimal_or_absent() {
    // NOTES.md references should be minimal or absent (isolation mode removes these files)
    let fix_prompt = prompt_fix("", "", "");

    // Fix prompt may have optional language or no reference
    // It uses "(if it exists)" when referencing NOTES.md
    if fix_prompt.contains("NOTES.md") {
        assert!(
            fix_prompt.contains("if it exists") || fix_prompt.contains("Optionally"),
            "Fix prompt NOTES.md reference should be optional"
        );
    }
}

#[test]
fn test_fix_prompt_contains_constraint_language() {
    // Verify that fix prompt contains explicit constraint language
    let fix_prompt = prompt_fix("", "", "");
    assert!(
        fix_prompt.contains("MUST NOT") || fix_prompt.contains("DO NOT"),
        "Fix prompt should contain explicit constraint language (MUST NOT or DO NOT)"
    );
    assert!(
        fix_prompt.contains("CRITICAL CONSTRAINTS"),
        "Fix prompt should contain a CRITICAL CONSTRAINTS section"
    );
}

#[test]
fn test_fix_prompt_forbids_exploration() {
    // Verify that fix prompt explicitly forbids repository exploration
    let fix_prompt = prompt_fix("", "", "");
    assert!(
        fix_prompt.contains("MUST NOT modify the ISSUES content")
            || fix_prompt.contains("LIMITEDLY")
            || fix_prompt.contains("stop exploring"),
        "Fix prompt should explicitly forbid unbounded exploration or limit it"
    );
}

#[test]
fn test_fix_prompt_instructs_to_only_work_on_issues_files() {
    // Verify that fix prompt instructs to only work on files from ISSUES
    let fix_prompt = prompt_fix("", "", "test issues");
    assert!(
        fix_prompt.contains("test issues"),
        "Fix prompt should contain the embedded issues content"
    );
    assert!(
        fix_prompt.contains("ONLY") || fix_prompt.contains("only"),
        "Fix prompt should instruct to only work on specific files"
    );
    // Updated to match new constraint language that references FILES YOU MAY MODIFY
    assert!(
        fix_prompt.contains("FILES YOU MAY MODIFY")
            || fix_prompt.contains("embedded ISSUES content"),
        "Fix prompt should limit work to specific files from ISSUES"
    );
}

#[test]
fn test_fix_prompt_forbids_running_commands() {
    // Verify that fix prompt explicitly forbids running commands
    let fix_prompt = prompt_fix("", "", "");
    let command_patterns = ["git", "ls", "find", "cat", "DO NOT run any commands"];
    let has_command_constraint = command_patterns
        .iter()
        .any(|pattern| fix_prompt.contains(pattern));
    assert!(
        has_command_constraint,
        "Fix prompt should explicitly forbid running commands"
    );
}

#[test]
fn test_fix_prompt_is_template_based() {
    // Verify that fix prompt uses template-based approach (not hardcoded string)
    let fix_prompt = prompt_fix("", "", "");
    // If template loading failed, we'd get an empty string
    assert!(
        !fix_prompt.is_empty(),
        "Fix prompt should not be empty (template loading should succeed)"
    );
    assert!(
        fix_prompt.contains("FIX MODE"),
        "Fix prompt should contain FIX MODE indicator"
    );
}

#[test]
fn test_fix_prompt_includes_file_list_from_issues() {
    // Verify that fix prompt includes extracted file list
    let issues = r"
# Issues
- [ ] [src/main.rs:42] Bug in main function
- [ ] [src/lib.rs:10] Style issue
";
    let fix_prompt = prompt_fix("", "", issues);
    assert!(
        fix_prompt.contains("FILES YOU MAY MODIFY"),
        "Fix prompt should include file list header"
    );
    assert!(
        fix_prompt.contains("src/main.rs"),
        "Fix prompt should list extracted files"
    );
    assert!(
        fix_prompt.contains("src/lib.rs"),
        "Fix prompt should list all extracted files"
    );
}

#[test]
fn test_fix_prompt_handles_empty_file_list() {
    // Verify that fix prompt handles empty file list gracefully
    let issues = "# Issues\n- [ ] Fix the build system";
    let fix_prompt = prompt_fix("", "", issues);
    assert!(
        fix_prompt.contains("No specific files were extracted"),
        "Fix prompt should indicate no specific files when extraction finds none"
    );
    assert!(
        !fix_prompt.contains("FULL AUTO MODE"),
        "Fix prompt must not widen permissions when extraction yields zero paths"
    );
    assert!(
        fix_prompt.contains("ONLY work on files mentioned in the ISSUES content"),
        "Fix prompt must preserve containment even without extracted paths"
    );
}

#[test]
fn test_fix_prompt_allows_reading_listed_files() {
    // Verify that fix prompt explicitly allows reading listed files
    let issues = r"
# Issues
- [ ] [src/main.rs:42] Bug in main function
";
    let fix_prompt = prompt_fix("", "", issues);
    // Updated to match new constraint language that references FILES YOU MAY MODIFY
    assert!(
        fix_prompt.contains("MAY read the files listed")
            || fix_prompt.contains("FILES YOU MAY MODIFY"),
        "Fix prompt should explicitly allow reading listed files"
    );
}

#[test]
fn test_fix_prompt_still_prohibits_exploration() {
    // Verify that fix prompt still prohibits exploration commands
    let fix_prompt = prompt_fix("", "", "");
    // The XML template allows LIMITED exploration for vague issue descriptions
    // but emphasizes stopping once relevant code is found
    assert!(
        fix_prompt.contains("stop exploring")
            || fix_prompt.contains("LIMITEDLY")
            || fix_prompt.contains("MUST stop exploring"),
        "Fix prompt should emphasize limited exploration"
    );
    // The template says "use grep to find function/class names"
    assert!(
        fix_prompt.contains("grep")
            || fix_prompt.contains("ripgrep")
            || fix_prompt.contains("locate"),
        "Fix prompt should explicitly allow discovery tools for finding relevant code"
    );
}

#[test]
fn test_fix_prompt_file_list_is_sorted() {
    // Verify that file list is sorted alphabetically
    let issues = r"
# Issues
- [ ] [src/zebra.rs:1] Z file
- [ ] [src/alpha.rs:1] A file
- [ ] [src/beta.rs:1] B file
";
    let fix_prompt = prompt_fix("", "", issues);
    // Find the file list section
    let files_start = fix_prompt.find("FILES YOU MAY MODIFY").unwrap();
    let files_section = &fix_prompt[files_start..];

    // Check that alpha appears before beta before zebra
    let alpha_pos = files_section.find("src/alpha.rs").unwrap();
    let beta_pos = files_section.find("src/beta.rs").unwrap();
    let zebra_pos = files_section.find("src/zebra.rs").unwrap();

    assert!(
        alpha_pos < beta_pos && beta_pos < zebra_pos,
        "File list should be sorted alphabetically"
    );
}

#[test]
fn test_fix_prompt_deduplicates_files() {
    // Verify that duplicate file references are deduplicated
    let issues = r"
# Issues
- [ ] [src/main.rs:42] First issue
- [ ] [src/main.rs:100] Second issue (same file)
- [ ] [src/lib.rs:10] Third issue
";
    let fix_prompt = prompt_fix("", "", issues);
    // Count occurrences of src/main.rs in the file list section
    let files_start = fix_prompt.find("FILES YOU MAY MODIFY").unwrap();
    let files_section = &fix_prompt[files_start..];

    let main_count = files_section.matches("src/main.rs").count();
    assert_eq!(
        main_count, 1,
        "File should appear only once in the list (deduplicated)"
    );
}

#[test]
fn test_fix_prompt_explicitly_states_content_is_embedded() {
    let fix_prompt = prompt_fix("", "", "");
    assert!(
        fix_prompt.contains("ISSUES FROM REVIEW")
            || fix_prompt.contains("provided for reference only"),
        "Fix prompt should explicitly state ISSUES content is embedded in the prompt"
    );
}

#[test]
fn test_fix_prompt_tells_agent_not_to_modify_issues_file() {
    let fix_prompt = prompt_fix("", "", "");
    assert!(
        fix_prompt.contains("MUST NOT modify ISSUES")
            || fix_prompt.contains("DO NOT modify")
            || fix_prompt.contains("provided for reference"),
        "Fix prompt should explicitly tell agent not to modify the ISSUES file"
    );
}

#[test]
fn test_fix_prompt_references_file_list_section_explicitly() {
    let fix_prompt = prompt_fix("prompt", "plan", "issues");
    assert!(
        fix_prompt.contains("FILES YOU MAY MODIFY"),
        "Fix prompt should explicitly reference the FILES YOU MAY MODIFY section"
    );
}

#[test]
fn test_prompt_fix_with_context() {
    use crate::workspace::MemoryWorkspace;

    let workspace = MemoryWorkspace::new_test();
    let context = TemplateContext::default();
    let session_caps = SessionCapabilities::new(
        &CapabilitySet::defaults_for_drain(SessionDrain::Fix),
        &PolicyFlagSet::defaults_for_drain(SessionDrain::Fix),
    );
    let result = prompt_fix_with_context(
        &context,
        "test prompt content",
        "test plan content",
        "test issues content",
        &workspace,
        session_caps,
    );
    assert!(result.contains("test issues content"));
    assert!(result.contains("MUST NOT modify the ISSUES content"));
    assert!(result.contains("provided for reference only"));
    assert!(
        result.contains("getting rid of tech debt is necessary to fix a bug"),
        "Fix prompt should instruct agent to do necessary refactors"
    );
}

#[test]
fn test_prompt_fix_with_context_empty() {
    use crate::workspace::MemoryWorkspace;
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let session_caps = SessionCapabilities::new(
        &CapabilitySet::defaults_for_drain(SessionDrain::Fix),
        &PolicyFlagSet::defaults_for_drain(SessionDrain::Fix),
    );
    let result = prompt_fix_with_context(&context, "", "", "", &workspace, session_caps);
    assert!(result.contains("FIX MODE"));
    assert!(!result.is_empty());
}

#[test]
fn test_context_based_fix_matches_regular() {
    use crate::workspace::MemoryWorkspace;
    let context = TemplateContext::new(crate::prompts::template_registry::TemplateRegistry::new(
        None,
    ));
    let workspace = MemoryWorkspace::new_test();
    let session_caps = SessionCapabilities::new(
        &CapabilitySet::defaults_for_drain(SessionDrain::Fix),
        &PolicyFlagSet::defaults_for_drain(SessionDrain::Fix),
    );
    let regular = prompt_fix("prompt", "plan", "issues");
    let with_context = prompt_fix_with_context(&context, "prompt", "plan", "issues", &workspace, session_caps);
    // Normalize absolute paths to avoid cross-test current_dir races.
    let normalize_paths = |input: &str| {
        let xml_re = Regex::new(r"[^\s`]*\.agent/tmp/fix_result\.xml").expect("xml regex");
        let xsd_re = Regex::new(r"[^\s`]*\.agent/tmp/fix_result\.xsd").expect("xsd regex");
        let normalized = xml_re.replace_all(input, "<FIX_RESULT_XML_PATH>");
        let normalized = xsd_re.replace_all(&normalized, "<FIX_RESULT_XSD_PATH>");
        normalized.into_owned()
    };
    // Both should produce equivalent output aside from absolute path prefixes.
    assert_eq!(normalize_paths(&regular), normalize_paths(&with_context));
}

#[test]
fn test_prompt_generate_commit_message_with_diff_with_context() {
    let context = TemplateContext::default();
    // Use MemoryWorkspace instead of WorkspaceFs - no real filesystem access needed
    let workspace = MemoryWorkspace::new_test();
    let (capabilities, policy_flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let diff = "diff --git a/src/main.rs b/src/main.rs\n+fn new_func() {}";
    let result = prompt_generate_commit_message_with_diff_with_context(&context, diff, &workspace, session_caps);
    assert!(!result.is_empty());
    assert!(result.contains("DIFF:") || result.contains("diff"));
    assert!(!result.contains("ERROR: Empty diff"));

    // Shared partials should be expanded
    assert!(
        result.contains("*** NO-EXECUTE MODE - READ ONLY"),
        "commit_message_xml should render shared/_safety_no_execute partial"
    );
    assert!(
        result.contains("*** UNATTENDED MODE - NO USER INTERACTION ***"),
        "commit_message_xml should render shared/_unattended_mode partial"
    );
    assert!(
        !result.contains("{{>"),
        "commit_message_xml should not contain raw partial directives"
    );

    assert!(
        result.contains("authorized to write") || result.contains("AUTHORIZATION"),
        "commit_message_xml should explicitly authorize writing commit_message.xml"
    );
    assert!(
        result.contains("READ-ONLY")
            && (result.contains("EXCEPT FOR writing")
                || result.contains("except for writing")
                || result.contains("Except for writing"))
            && result.contains("commit_message.xml"),
        "commit_message_xml should be read-only except for writing commit_message.xml"
    );

    assert!(
        !result.contains("DO NOT print")
            && !result.contains("Do NOT print")
            && !result.contains("ONLY acceptable output")
            && !result.contains("The ONLY acceptable output"),
        "commit_message_xml should not include stdout suppression wording"
    );

    assert!(
        result.contains("MANDATORY") && result.contains("OVERRIDES the safety mode"),
        "commit_message_xml should mark file write mandatory and explicitly override safety mode"
    );
    assert!(
        result.contains("does NOT override")
            && (result.contains("analyze") || result.contains("DIFF")),
        "commit_message_xml should clarify that mandatory write does not override task requirements"
    );

    assert!(
        (result.contains("not writing") || result.contains("Not writing"))
            && result.contains("FAILURE"),
        "commit_message_xml should state that failing to write XML is a FAILURE"
    );
    assert!(
        result.contains("does not conform")
            && (result.contains("XSD") || result.contains("schema"))
            && result.contains("FAILURE"),
        "commit_message_xml should state that non-XSD-conformant XML is a FAILURE"
    );
}

#[test]
fn test_prompt_generate_commit_message_with_diff_with_context_empty() {
    let context = TemplateContext::default();
    // Use MemoryWorkspace instead of WorkspaceFs - no real filesystem access needed
    let workspace = MemoryWorkspace::new_test();
    let (capabilities, policy_flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let result = prompt_generate_commit_message_with_diff_with_context(&context, "", &workspace, session_caps);
    assert!(result.contains("ERROR: Empty diff"));
}

#[test]
fn test_context_based_commit_uses_workspace_paths() {
    let context = TemplateContext::default();
    // Use MemoryWorkspace instead of WorkspaceFs - no real filesystem access needed
    let workspace = MemoryWorkspace::new_test();
    let (capabilities, policy_flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let diff = "diff --git a/src/main.rs b/src/main.rs\n+fn new_func() {}";
    let result = prompt_generate_commit_message_with_diff_with_context(&context, diff, &workspace, session_caps);
    // Verify the prompt uses absolute paths from workspace
    assert!(
        result.contains("/test/repo/.agent/tmp/commit_message.xml")
            || result.contains("/test/repo/.agent/tmp/commit_message.xsd"),
        "Prompt should contain absolute paths from workspace"
    );
}

#[test]
fn test_commit_xsd_retry_prompt_warning_prefix_is_ascii_only() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();

    let result = prompt_commit_xsd_retry_with_context(&context, "xsd error", &workspace);

    assert!(
        result.contains("WARNING: Required XSD retry files are missing:"),
        "prompt should include a deterministic WARNING prefix when retry artifacts are missing"
    );
    assert!(
        !result.contains('⚠'),
        "prompt must not include non-ASCII glyphs"
    );
}

#[test]
fn commit_message_xsd_allows_code_in_skip_reason() {
    // The Rust validator reads text via helpers that support inline <code> elements.
    // The published schema must match by typing ralph-skip as TextWithCodeType.
    assert!(
        super::COMMIT_MESSAGE_XSD_SCHEMA
            .contains("<xs:element name=\"ralph-skip\" type=\"TextWithCodeType\""),
        "commit_message.xsd must type ralph-skip as TextWithCodeType"
    );
}

#[test]
fn commit_message_xsd_disallows_mixed_simple_and_detailed_body_forms() {
    // The Rust validator rejects mixing <ralph-body> with detailed tags.
    // The schema should not model them as siblings in the same sequence.
    // The old schema modelled these as adjacent elements in the same sequence.
    // We assert that exact permissive pattern is gone.
    let old_permissive_pattern = Regex::new(
        r#"(?s)<xs:element\s+name=\"ralph-body\"\s+type=\"TextWithCodeType\"\s+minOccurs=\"0\"\s*/>\s*<xs:element\s+name=\"ralph-body-summary\""#,
    )
    .expect("regex");

    assert!(
        !old_permissive_pattern.is_match(super::COMMIT_MESSAGE_XSD_SCHEMA),
        "commit_message.xsd must not allow ralph-body and detailed tags in the same sequence"
    );
}

#[test]
fn commit_message_xsd_is_valid_xsd10_structure() {
    // Standard XSD 1.0 validators reject `xs:all` in many nested positions.
    // Keep this schema within the widely supported subset so external tooling can compile it.
    assert!(
        !super::COMMIT_MESSAGE_XSD_SCHEMA.contains("<xs:all"),
        "commit_message.xsd must not use xs:all (not reliably supported in nested structures)"
    );
}

#[test]
fn commit_message_xsd_excluded_file_uses_simple_content_extension() {
    // Some XSD processors are stricter about "mixed=true + attribute only" complex types.
    // Model excluded-file text via xs:simpleContent so text-with-attributes is unambiguous.
    let pattern = Regex::new(
        r#"(?s)<xs:complexType\s+name=\"ExcludedFileEntryType\">\s*<xs:simpleContent>\s*<xs:extension\s+base=\"xs:string\">\s*<xs:attribute\s+name=\"reason\""#,
    )
    .expect("regex");
    assert!(
        pattern.is_match(super::COMMIT_MESSAGE_XSD_SCHEMA),
        "ExcludedFileEntryType should use xs:simpleContent extension"
    );
}

#[test]
fn commit_message_xsd_allows_detailed_body_elements_in_any_order() {
    // LLMs often reorder optional sibling tags; keep the published schema aligned with the Rust
    // validator which accepts detailed body tags in any order.
    let pattern = Regex::new(
        r#"(?s)<xs:choice\s+minOccurs=\"0\"\s+maxOccurs=\"3\">\s*<xs:element\s+name=\"ralph-body-summary\".*?<xs:element\s+name=\"ralph-body-details\".*?<xs:element\s+name=\"ralph-body-footer\""#,
    )
    .expect("regex");

    assert!(
        pattern.is_match(super::COMMIT_MESSAGE_XSD_SCHEMA),
        "Detailed body tags should be modelled as a repeating choice (order-insensitive)"
    );
}

#[test]
fn commit_message_xsd_allows_files_before_body_block() {
    // The Rust validator accepts ralph-files / ralph-excluded-files regardless of sibling order.
    // The published schema should allow files/excluded metadata to appear before body tags.
    let pattern = Regex::new(
        r#"(?s)<xs:sequence>.*name=\"ralph-subject\".*name=\"ralph-files\".*name=\"ralph-body\""#,
    )
    .expect("regex");

    assert!(
        pattern.is_match(super::COMMIT_MESSAGE_XSD_SCHEMA),
        "Schema should allow ralph-files to appear before ralph-body/detailed tags"
    );
}
