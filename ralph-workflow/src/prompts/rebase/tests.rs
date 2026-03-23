use super::*;
use crate::workspace::MemoryWorkspace;

#[test]
fn test_build_conflict_resolution_prompt_no_mentions_rebase() {
    let conflicts = HashMap::new();
    let prompt = build_conflict_resolution_prompt(&conflicts, None, None);

    // The prompt should NOT mention "rebase" or "rebasing"
    assert!(!prompt.to_lowercase().contains("rebase"));
    assert!(!prompt.to_lowercase().contains("rebasing"));

    // But it SHOULD mention "merge conflict"
    assert!(prompt.to_lowercase().contains("merge conflict"));
}

#[test]
fn test_build_conflict_resolution_prompt_with_context() {
    let conflicts = [(
        "test.rs".to_string(),
        FileConflict {
            conflict_content: "<<<<<<< ours\nfn foo() {}\n=======\nfn bar() {}\n>>>>>>> theirs"
                .to_string(),
            current_content: "<<<<<<< ours\nfn foo() {}\n=======\nfn bar() {}\n>>>>>>> theirs"
                .to_string(),
        },
    )]
    .into_iter()
    .collect::<HashMap<_, _>>();

    let prompt_md = "Add a new feature";
    let plan = "1. Create foo function\n2. Create bar function";

    let prompt = build_conflict_resolution_prompt(&conflicts, Some(prompt_md), Some(plan));

    // Should include context from PROMPT.md
    assert!(prompt.contains("Add a new feature"));

    // Should include context from PLAN.md
    assert!(prompt.contains("Create foo function"));
    assert!(prompt.contains("Create bar function"));

    // Should include the conflicted file
    assert!(prompt.contains("test.rs"));

    // Should NOT mention rebase
    assert!(!prompt.to_lowercase().contains("rebase"));
}

#[test]
fn test_get_language_marker() {
    assert_eq!(get_language_marker("file.rs"), "rust");
    assert_eq!(get_language_marker("file.py"), "python");
    assert_eq!(get_language_marker("file.js"), "javascript");
    assert_eq!(get_language_marker("file.ts"), "typescript");
    assert_eq!(get_language_marker("file.go"), "go");
    assert_eq!(get_language_marker("file.java"), "java");
    assert_eq!(get_language_marker("file.cpp"), "cpp");
    assert_eq!(get_language_marker("file.md"), "markdown");
    assert_eq!(get_language_marker("file.yaml"), "yaml");
    assert_eq!(get_language_marker("file.unknown"), "");
}

#[test]
fn collect_conflict_info_with_workspace_reads_files_via_workspace() {
    let content = "<<<<<<< ours\nfn a() {}\n=======\nfn b() {}\n>>>>>>> theirs\n";
    let workspace = MemoryWorkspace::new_test().with_file("src/lib.rs", content);

    let conflicts = collect_conflict_info_with_workspace(&workspace, &["src/lib.rs".into()])
        .expect("should collect conflict info");

    let c = conflicts.get("src/lib.rs").expect("missing conflict entry");
    assert_eq!(c.current_content, content);
    assert!(c.conflict_content.contains("<<<<<<<"));
    assert!(c.conflict_content.contains("======="));
    assert!(c.conflict_content.contains(">>>>>>>"));
}

#[test]
fn test_format_context_section_with_both() {
    let prompt_md = "Test prompt";
    let plan = "Test plan";
    let context = format_context_section(Some(prompt_md), Some(plan));

    assert!(context.contains("## Task Context"));
    assert!(context.contains("Test prompt"));
    assert!(context.contains("## Implementation Plan"));
    assert!(context.contains("Test plan"));
}

#[test]
fn test_format_context_section_with_prompt_only() {
    let prompt_md = "Test prompt";
    let context = format_context_section(Some(prompt_md), None);

    assert!(context.contains("## Task Context"));
    assert!(context.contains("Test prompt"));
    assert!(!context.contains("## Implementation Plan"));
}

#[test]
fn test_format_context_section_with_plan_only() {
    let plan = "Test plan";
    let context = format_context_section(None, Some(plan));

    assert!(!context.contains("## Task Context"));
    assert!(context.contains("## Implementation Plan"));
    assert!(context.contains("Test plan"));
}

#[test]
fn test_format_context_section_empty() {
    let context = format_context_section(None, None);
    assert!(context.is_empty());
}

#[test]
fn test_format_conflicts_section() {
    let conflicts = [(
        "src/test.rs".to_string(),
        FileConflict {
            conflict_content: "<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs".to_string(),
            current_content: "<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs".to_string(),
        },
    )]
    .into_iter()
    .collect::<HashMap<_, _>>();

    let section = format_conflicts_section(&conflicts);

    assert!(section.contains("### src/test.rs"));
    assert!(section.contains("Current state (with conflict markers)"));
    assert!(section.contains("```rust"));
    assert!(section.contains("<<<<<<< ours"));
    assert!(section.contains("Conflict sections"));
}

#[test]
fn test_template_is_used() {
    // Verify that the template-based approach produces valid output
    let conflicts = HashMap::new();
    let prompt = build_conflict_resolution_prompt(&conflicts, None, None);

    // Should contain key sections from the template
    assert!(prompt.contains("# MERGE CONFLICT RESOLUTION"));
    assert!(prompt.contains("## Conflict Resolution Instructions"));
    assert!(prompt.contains("## Optional JSON Output Format"));
    assert!(prompt.contains("resolved_files"));
}

#[test]
fn test_build_conflict_resolution_prompt_with_registry_context() {
    let context = TemplateContext::default();
    let conflicts = HashMap::new();
    let prompt = build_conflict_resolution_prompt_with_context(&context, &conflicts, None, None);

    // The prompt should NOT mention "rebase" or "rebasing"
    assert!(!prompt.to_lowercase().contains("rebase"));
    assert!(!prompt.to_lowercase().contains("rebasing"));

    // But it SHOULD mention "merge conflict"
    assert!(prompt.to_lowercase().contains("merge conflict"));
}

#[test]
fn test_build_conflict_resolution_prompt_with_registry_context_and_content() {
    let context = TemplateContext::default();
    let conflicts = [(
        "test.rs".to_string(),
        FileConflict {
            conflict_content: "<<<<<<< ours\nfn foo() {}\n=======\nfn bar() {}\n>>>>>>> theirs"
                .to_string(),
            current_content: "<<<<<<< ours\nfn foo() {}\n=======\nfn bar() {}\n>>>>>>> theirs"
                .to_string(),
        },
    )]
    .into_iter()
    .collect::<HashMap<_, _>>();

    let prompt_md = "Add a new feature";
    let plan = "1. Create foo function\n2. Create bar function";

    let prompt = build_conflict_resolution_prompt_with_context(
        &context,
        &conflicts,
        Some(prompt_md),
        Some(plan),
    );

    // Should include context from PROMPT.md
    assert!(prompt.contains("Add a new feature"));

    // Should include context from PLAN.md
    assert!(prompt.contains("Create foo function"));
    assert!(prompt.contains("Create bar function"));

    // Should include the conflicted file
    assert!(prompt.contains("test.rs"));

    // Should NOT mention rebase
    assert!(!prompt.to_lowercase().contains("rebase"));
}

#[test]
fn test_registry_context_based_matches_regular() {
    let context = TemplateContext::default();
    let conflicts = [(
        "test.rs".to_string(),
        FileConflict {
            conflict_content: "conflict".to_string(),
            current_content: "current".to_string(),
        },
    )]
    .into_iter()
    .collect::<HashMap<_, _>>();

    let regular = build_conflict_resolution_prompt(&conflicts, Some("prompt"), Some("plan"));
    let with_context = build_conflict_resolution_prompt_with_context(
        &context,
        &conflicts,
        Some("prompt"),
        Some("plan"),
    );
    // Both should produce equivalent output
    assert_eq!(regular, with_context);
}

#[test]
fn test_branch_info_struct_exists() {
    let info = BranchInfo {
        current_branch: "feature".to_string(),
        upstream_branch: "main".to_string(),
        current_commits: vec!["abc123 feat: add thing".to_string()],
        upstream_commits: vec!["def456 fix: bug".to_string()],
        diverging_count: 5,
    };
    assert_eq!(info.current_branch, "feature");
    assert_eq!(info.diverging_count, 5);
}

#[test]
fn test_format_branch_info_section() {
    let info = BranchInfo {
        current_branch: "feature".to_string(),
        upstream_branch: "main".to_string(),
        current_commits: vec!["abc123 feat: add thing".to_string()],
        upstream_commits: vec!["def456 fix: bug".to_string()],
        diverging_count: 5,
    };

    let section = format_branch_info_section(&info);

    assert!(section.contains("Branch Information"));
    assert!(section.contains("feature"));
    assert!(section.contains("main"));
    assert!(section.contains('5'));
    assert!(section.contains("abc123"));
    assert!(section.contains("def456"));
}

#[test]
fn test_enhanced_prompt_with_branch_info() {
    let context = TemplateContext::default();
    let conflicts = [(
        "test.rs".to_string(),
        FileConflict {
            conflict_content: "conflict".to_string(),
            current_content: "current".to_string(),
        },
    )]
    .into_iter()
    .collect::<HashMap<_, _>>();

    let branch_info = BranchInfo {
        current_branch: "feature".to_string(),
        upstream_branch: "main".to_string(),
        current_commits: vec!["abc123 my change".to_string()],
        upstream_commits: vec!["def456 their change".to_string()],
        diverging_count: 3,
    };

    let prompt = build_enhanced_conflict_resolution_prompt(
        &context,
        &conflicts,
        Some(&branch_info),
        None,
        None,
    );

    // Should include branch information
    assert!(prompt.contains("Branch Information"));
    assert!(prompt.contains("feature"));
    assert!(prompt.contains("main"));
    assert!(prompt.contains('3')); // diverging count

    // Should NOT mention rebase
    assert!(!prompt.to_lowercase().contains("rebase"));
}
