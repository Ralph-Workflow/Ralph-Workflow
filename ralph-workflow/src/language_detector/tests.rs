//! Tests for language detection module.

use super::*;
use crate::workspace::MemoryWorkspace;
use std::path::Path;

// Extension mapping tests
#[test]
fn extension_to_language_covers_common_languages() {
    assert_eq!(extension_to_language("rs"), Some("Rust"));
    assert_eq!(extension_to_language("py"), Some("Python"));
    assert_eq!(extension_to_language("js"), Some("JavaScript"));
    assert_eq!(extension_to_language("ts"), Some("TypeScript"));
    assert_eq!(extension_to_language("go"), Some("Go"));
    assert_eq!(extension_to_language("java"), Some("Java"));
    assert_eq!(extension_to_language("rb"), Some("Ruby"));
    assert_eq!(extension_to_language("php"), Some("PHP"));
    assert_eq!(extension_to_language("yml"), Some("YAML"));
    assert_eq!(extension_to_language("yaml"), Some("YAML"));
    assert_eq!(extension_to_language("json"), Some("JSON"));
    assert_eq!(extension_to_language("md"), None);
}

#[test]
fn extension_matching_is_case_insensitive() {
    assert_eq!(extension_to_language("RS"), Some("Rust"));
    assert_eq!(extension_to_language("Py"), Some("Python"));
    assert_eq!(extension_to_language("JS"), Some("JavaScript"));
}

// Stack detection tests
#[test]
fn primary_language_prefers_code_over_config() {
    // Many config/markup files and a single Rust file.
    let workspace = MemoryWorkspace::new_test()
        .with_file("config/0.yml", "")
        .with_file("config/1.yml", "")
        .with_file("config/2.yml", "")
        .with_file("config/3.yml", "")
        .with_file("config/4.yml", "")
        .with_file("config/5.yml", "")
        .with_file("config/6.yml", "")
        .with_file("config/7.yml", "")
        .with_file("config/8.yml", "")
        .with_file("config/9.yml", "")
        .with_file("src/main.rs", "fn main() {}");

    let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();
    assert_eq!(stack.primary_language, "Rust");
    assert!(stack.secondary_languages.contains(&"YAML".to_string()));
}

#[test]
fn rust_project_detection() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(
            "Cargo.toml",
            r#"
[package]
name = "test"
version = "0.1.0"

[dependencies]
tokio = "1.0"
"#,
        )
        .with_file("src/main.rs", "fn main() {}")
        .with_file("tests/integration.rs", "#[test] fn test() {}");

    let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();
    assert_eq!(stack.primary_language, "Rust");
    assert!(stack.frameworks.contains(&"Tokio".to_string()));
    assert_eq!(stack.package_manager, Some("Cargo".to_string()));
    assert!(stack.has_tests);
}

#[test]
fn python_project_detection() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(
            "pyproject.toml",
            r#"
[project]
name = "test"
[project.dependencies]
django = "*"
"#,
        )
        .with_file("app/main.py", "")
        .with_file("tests/test_app.py", "");

    let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();
    assert_eq!(stack.primary_language, "Python");
    assert!(stack.frameworks.contains(&"Django".to_string()));
    assert!(stack.has_tests);
}

#[test]
fn react_project_detection() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(
            "package.json",
            r#"{"name":"test","dependencies":{"react":"^18.0.0"},"devDependencies":{"jest":"^29.0.0"}}"#,
        )
        .with_file("src/App.tsx", "")
        .with_file("src/index.ts", "");

    let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();
    assert!(stack.is_javascript_or_typescript());
    assert!(stack.frameworks.contains(&"React".to_string()));
    assert_eq!(stack.package_manager, Some("npm".to_string()));
    assert_eq!(stack.test_framework, Some("Jest".to_string()));
}

#[test]
fn go_project_detection() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(
            "go.mod",
            "module example.com/test\n\ngo 1.21\n\nrequire github.com/gin-gonic/gin v1.9.0\n",
        )
        .with_file("main.go", "package main")
        .with_file("handlers/api.go", "package handlers");

    let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();
    assert_eq!(stack.primary_language, "Go");
    assert!(stack.frameworks.contains(&"Gin".to_string()));
}

#[test]
fn monorepo_multiple_packages_detects_primary_language_by_prevalence() {
    // Frontend in TypeScript (more files => primary).
    // Backend in Go.
    // Shared scripts in Python.
    let workspace = MemoryWorkspace::new_test()
        .with_file("backend/go.mod", "module example.com/backend\n\ngo 1.21")
        .with_file("backend/main.go", "package main")
        .with_file("frontend/package.json", r#"{"name":"frontend"}"#)
        .with_file("frontend/src/App.tsx", "")
        .with_file("frontend/src/index.ts", "")
        .with_file("frontend/src/utils.ts", "")
        .with_file("scripts/deploy.py", "");

    let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();
    assert_eq!(stack.primary_language, "TypeScript");
    assert!(stack.secondary_languages.contains(&"Go".to_string()));
    assert!(stack.secondary_languages.contains(&"Python".to_string()));
}

#[test]
fn ignores_node_modules_and_target_like_directories() {
    // A real JS file.
    // Many files in node_modules should be ignored.
    // Many files in target should be ignored.
    let workspace = MemoryWorkspace::new_test().with_file("src/index.js", "export default {}");

    (0..50).for_each(|i| {
        workspace
            .clone()
            .with_file(&format!("node_modules/pkg{i}/index.js"), "");
    });
    (0..50).for_each(|i| {
        workspace
            .clone()
            .with_file(&format!("target/build{i}/main.rs"), "");
    });

    let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();
    assert_eq!(stack.primary_language, "JavaScript");
}
