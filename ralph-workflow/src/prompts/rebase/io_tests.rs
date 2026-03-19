use super::*;
use crate::workspace::MemoryWorkspace;

#[test]
fn test_collect_conflict_info_with_workspace() {
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
fn test_collect_conflict_info_with_workspace_multiple_files() {
    let workspace = MemoryWorkspace::new_test()
        .with_file("a.rs", "<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n")
        .with_file("b.rs", "<<<<<<< ours\nc\n=======\nd\n>>>>>>> theirs\n");

    let conflicts =
        collect_conflict_info_with_workspace(&workspace, &["a.rs".into(), "b.rs".into()])
            .expect("should collect conflict info");

    assert_eq!(conflicts.len(), 2);
    assert!(conflicts.contains_key("a.rs"));
    assert!(conflicts.contains_key("b.rs"));
}

#[test]
fn test_collect_conflict_info_with_workspace_empty_conflict() {
    let content = "fn main() {}\n";
    let workspace = MemoryWorkspace::new_test().with_file("src/lib.rs", content);

    let conflicts = collect_conflict_info_with_workspace(&workspace, &["src/lib.rs".into()])
        .expect("should collect conflict info");

    let c = conflicts.get("src/lib.rs").expect("missing conflict entry");
    assert_eq!(c.current_content, content);
    assert!(c.conflict_content.is_empty());
}
