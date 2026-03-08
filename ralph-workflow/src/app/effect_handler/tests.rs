use super::*;

#[test]
fn test_real_handler_default() {
    let handler = RealAppEffectHandler::default();
    assert!(handler.workspace_root.is_none());
}

#[test]
fn test_real_handler_with_workspace_root() {
    let root = PathBuf::from("/some/path");
    let handler = RealAppEffectHandler::with_workspace_root(root.clone());
    assert_eq!(handler.workspace_root, Some(root));
}

#[test]
fn test_resolve_path_absolute() {
    let handler = RealAppEffectHandler::with_workspace_root(PathBuf::from("/workspace"));
    let absolute = PathBuf::from("/absolute/path");
    assert_eq!(handler.resolve_path(&absolute), absolute);
}

#[test]
fn test_resolve_path_relative_with_root() {
    let handler = RealAppEffectHandler::with_workspace_root(PathBuf::from("/workspace"));
    let relative = PathBuf::from("relative/path");
    assert_eq!(
        handler.resolve_path(&relative),
        PathBuf::from("/workspace/relative/path")
    );
}

#[test]
fn test_resolve_path_relative_without_root() {
    let handler = RealAppEffectHandler::new();
    let relative = PathBuf::from("relative/path");
    assert_eq!(handler.resolve_path(&relative), relative);
}

#[test]
fn test_path_exists_effect() {
    let mut handler = RealAppEffectHandler::new();
    // Test with a path that definitely exists (this file's directory)
    let result = handler.execute(AppEffect::PathExists {
        path: PathBuf::from("."),
    });
    assert!(matches!(result, AppEffectResult::Bool(true)));
}

#[test]
fn test_path_not_exists_effect() {
    let mut handler = RealAppEffectHandler::new();
    let result = handler.execute(AppEffect::PathExists {
        path: PathBuf::from("/nonexistent/path/that/should/not/exist/12345"),
    });
    assert!(matches!(result, AppEffectResult::Bool(false)));
}

#[test]
fn test_get_env_var_effect() {
    let mut handler = RealAppEffectHandler::new();
    // PATH should always be set
    let result = handler.execute(AppEffect::GetEnvVar {
        name: "PATH".to_string(),
    });
    assert!(matches!(result, AppEffectResult::String(_)));
}

#[test]
fn test_get_env_var_not_set() {
    let mut handler = RealAppEffectHandler::new();
    let result = handler.execute(AppEffect::GetEnvVar {
        name: "DEFINITELY_NOT_SET_ENV_VAR_12345".to_string(),
    });
    assert!(matches!(result, AppEffectResult::Error(_)));
}

#[test]
fn test_set_env_var_effect() {
    let mut handler = RealAppEffectHandler::new();
    let var_name = "TEST_RALPH_ENV_VAR_12345";

    // Set the variable
    let result = handler.execute(AppEffect::SetEnvVar {
        name: var_name.to_string(),
        value: "test_value".to_string(),
    });
    assert!(matches!(result, AppEffectResult::Ok));

    // Verify it was set
    assert_eq!(std::env::var(var_name).ok(), Some("test_value".to_string()));

    // Clean up
    std::env::remove_var(var_name);
}

#[test]
fn test_logging_effects_are_noops() {
    let mut handler = RealAppEffectHandler::new();

    let effects = vec![
        AppEffect::LogInfo {
            message: "test".to_string(),
        },
        AppEffect::LogSuccess {
            message: "test".to_string(),
        },
        AppEffect::LogWarn {
            message: "test".to_string(),
        },
        AppEffect::LogError {
            message: "test".to_string(),
        },
    ];

    for effect in effects {
        let result = handler.execute(effect);
        assert!(
            matches!(result, AppEffectResult::Ok),
            "Logging effect should return Ok"
        );
    }
}

#[test]
fn test_rebase_effects_require_executor() {
    let mut handler = RealAppEffectHandler::new();

    let result = handler.execute(AppEffect::GitRebaseOnto {
        upstream_branch: "main".to_string(),
    });
    assert!(matches!(result, AppEffectResult::Error(_)));

    let result = handler.execute(AppEffect::GitContinueRebase);
    assert!(matches!(result, AppEffectResult::Error(_)));

    let result = handler.execute(AppEffect::GitAbortRebase);
    assert!(matches!(result, AppEffectResult::Error(_)));
}
