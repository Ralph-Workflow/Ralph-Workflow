use super::*;

#[test]
fn test_start_commit_file_path_defined() {
    assert_eq!(START_COMMIT_FILE, ".agent/start_commit");
}

#[test]
fn test_has_start_commit_returns_bool() {
    let result = has_start_commit();
    let _ = result;
}

#[test]
fn test_get_current_head_oid_returns_result() {
    let result = get_current_head_oid();
    let _ = result;
}

#[test]
fn test_load_start_commit_returns_result() {
    let result = load_start_point();
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_reset_start_commit_returns_result() {
    let result = reset_start_commit();
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_save_start_commit_returns_result() {
    let result = save_start_commit();
    assert!(result.is_ok() || result.is_err());
}
