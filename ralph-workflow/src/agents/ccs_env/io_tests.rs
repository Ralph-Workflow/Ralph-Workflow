use super::*;

// =========================================================================
// Mock-based tests using dependency injection
// =========================================================================

/// Mock environment for testing.
struct MockCcsEnv {
    vars: std::collections::HashMap<String, String>,
    home: Option<std::path::PathBuf>,
}

impl MockCcsEnv {
    fn new() -> Self {
        Self {
            vars: std::collections::HashMap::new(),
            home: None,
        }
    }

    fn with_home(mut self, home: std::path::PathBuf) -> Self {
        self.home = Some(home);
        self
    }
}

impl CcsEnvironment for MockCcsEnv {
    fn get_var(&self, name: &str) -> Option<String> {
        self.vars.get(name).cloned()
    }

    fn home_dir(&self) -> Option<std::path::PathBuf> {
        self.home.clone()
    }
}

/// Mock filesystem for testing.
struct MockCcsFs {
    files: std::collections::HashMap<std::path::PathBuf, String>,
}

impl MockCcsFs {
    fn new() -> Self {
        Self {
            files: std::collections::HashMap::new(),
        }
    }

    fn with_file(mut self, path: &str, content: &str) -> Self {
        self.files
            .insert(std::path::PathBuf::from(path), content.to_string());
        self
    }
}

impl CcsFilesystem for MockCcsFs {
    fn exists(&self, path: &std::path::Path) -> bool {
        self.files.contains_key(path)
    }

    fn read_to_string(&self, path: &std::path::Path) -> std::io::Result<String> {
        self.files
            .get(path)
            .cloned()
            .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "file not found"))
    }

    fn read_dir(&self, path: &std::path::Path) -> std::io::Result<Vec<CcsDirEntry>> {
        let mut entries = Vec::new();
        for file_path in self.files.keys() {
            if file_path.parent() == Some(path) {
                entries.push(CcsDirEntry {
                    path: file_path.clone(),
                    file_name: file_path
                        .file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .into_owned(),
                    is_file: true,
                });
            }
        }
        Ok(entries)
    }
}

#[test]
fn test_load_ccs_env_vars_with_mock_deps() {
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new()
        .with_file(
            "/mock/home/.ccs/config.json",
            r#"{"profiles":{"test":"test.settings.json"}}"#,
        )
        .with_file(
            "/mock/home/.ccs/test.settings.json",
            r#"{"env":{"API_KEY":"secret123","API_URL":"https://test.api"}}"#,
        );

    let result = load_ccs_env_vars_with_deps(&env, &fs, "test").unwrap();
    assert_eq!(result.get("API_KEY").unwrap(), "secret123");
    assert_eq!(result.get("API_URL").unwrap(), "https://test.api");
}

#[test]
fn test_load_ccs_env_vars_with_mock_yaml_config() {
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new()
        .with_file(
            "/mock/home/.ccs/config.yaml",
            r#"version: 7
profiles:
  yaml-profile:
    type: api
    settings: "yaml-profile.settings.json"
"#,
        )
        .with_file(
            "/mock/home/.ccs/yaml-profile.settings.json",
            r#"{"env":{"FROM_YAML":"yes"}}"#,
        );

    let result = load_ccs_env_vars_with_deps(&env, &fs, "yaml-profile").unwrap();
    assert_eq!(result.get("FROM_YAML").unwrap(), "yes");
}

#[test]
fn test_load_ccs_env_vars_with_mock_profile_not_found() {
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new();

    let result = load_ccs_env_vars_with_deps(&env, &fs, "nonexistent");
    assert!(result.is_err());
    match result.unwrap_err() {
        CcsEnvVarsError::ProfileNotFound { profile, .. } => {
            assert_eq!(profile, "nonexistent");
        }
        other => panic!("Expected ProfileNotFound, got: {other:?}"),
    }
}

#[test]
fn test_load_ccs_env_vars_with_mock_missing_home() {
    let env = MockCcsEnv::new();
    let fs = MockCcsFs::new();

    let result = load_ccs_env_vars_with_deps(&env, &fs, "any");
    assert!(result.is_err());
    match result.unwrap_err() {
        CcsEnvVarsError::MissingHomeDir => {}
        other => panic!("Expected MissingHomeDir, got: {other:?}"),
    }
}

#[test]
fn test_load_ccs_env_vars_with_mock_direct_settings_file() {
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new().with_file(
        "/mock/home/.ccs/direct.settings.json",
        r#"{"env":{"DIRECT_KEY":"direct_value"}}"#,
    );

    let result = load_ccs_env_vars_with_deps(&env, &fs, "direct").unwrap();
    assert_eq!(result.get("DIRECT_KEY").unwrap(), "direct_value");
}

#[test]
fn test_list_profiles_with_mock() {
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new()
        .with_file(
            "/mock/home/.ccs/config.json",
            r#"{"profiles":{"profile1":"p1.settings.json","profile2":"p2.settings.json"}}"#,
        )
        .with_file("/mock/home/.ccs/p1.settings.json", r#"{"env":{}}"#)
        .with_file("/mock/home/.ccs/p2.settings.json", r#"{"env":{}}"#)
        .with_file("/mock/home/.ccs/extra.settings.json", r#"{"env":{}}"#);

    let profiles = list_available_ccs_profiles_with_deps(&env, &fs);
    assert!(profiles.contains(&"profile1".to_string()));
    assert!(profiles.contains(&"profile2".to_string()));
    assert!(profiles.contains(&"extra".to_string()));
}

#[test]
fn test_load_ccs_env_vars_with_mock_yaml_nonstandard_indent() {
    // Test YAML config with 4-space indentation (nonstandard but valid)
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new()
        .with_file(
            "/mock/home/.ccs/config.yaml",
            r#"version: 7
profiles:
    indent:
        type: api
        settings: "indent.settings.json"
"#,
        )
        .with_file(
            "/mock/home/.ccs/indent.settings.json",
            r#"{"env":{"INDENT_KEY":"indent_value"}}"#,
        );

    let result = load_ccs_env_vars_with_deps(&env, &fs, "indent").unwrap();
    assert_eq!(result.get("INDENT_KEY").unwrap(), "indent_value");
}

#[test]
fn test_load_ccs_env_vars_with_mock_alternate_spelling() {
    // Test alternate spelling .setting.json (without 's')
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new().with_file(
        "/mock/home/.ccs/alternate.setting.json",
        r#"{"env":{"ALT_KEY":"alt_value"}}"#,
    );

    let result = load_ccs_env_vars_with_deps(&env, &fs, "alternate").unwrap();
    assert_eq!(result.get("ALT_KEY").unwrap(), "alt_value");
}

#[test]
fn test_load_ccs_env_vars_with_mock_missing_env_object() {
    // Test settings file without env object
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new().with_file(
        "/mock/home/.ccs/noenv.settings.json",
        r#"{"other_key":"other_value"}"#,
    );

    let result = load_ccs_env_vars_with_deps(&env, &fs, "noenv");
    assert!(result.is_err());
    match result.unwrap_err() {
        CcsEnvVarsError::MissingEnv { .. } => {}
        other => panic!("Expected MissingEnv error, got: {other:?}"),
    }
}

#[test]
fn test_load_ccs_env_vars_with_mock_empty_profile_name() {
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new();

    let result = load_ccs_env_vars_with_deps(&env, &fs, "");
    assert!(result.is_err());
    match result.unwrap_err() {
        CcsEnvVarsError::InvalidProfile { profile } => {
            assert_eq!(profile, "", "Expected empty profile name");
        }
        other => panic!("Expected InvalidProfile error, got: {other:?}"),
    }
}

#[test]
fn test_load_ccs_env_vars_with_mock_tilde_expansion() {
    // Test that tilde paths are expanded correctly
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new()
        .with_file(
            "/mock/home/.ccs/config.yaml",
            r#"version: 7
profiles:
  expand:
    type: api
    settings: "~/.ccs/expand.settings.json"
"#,
        )
        .with_file(
            "/mock/home/.ccs/expand.settings.json",
            r#"{"env":{"FROM_EXPAND":"success"}}"#,
        );

    let result = load_ccs_env_vars_with_deps(&env, &fs, "expand").unwrap();
    assert_eq!(result.get("FROM_EXPAND").unwrap(), "success");
}

#[test]
fn test_load_ccs_env_vars_with_mock_multiple_profiles() {
    // Test that multiple profile loads return independent results
    let env = MockCcsEnv::new().with_home(std::path::PathBuf::from("/mock/home"));
    let fs = MockCcsFs::new()
        .with_file(
            "/mock/home/.ccs/config.json",
            r#"{"profiles":{"profile_a":"a.settings.json","profile_b":"b.settings.json"}}"#,
        )
        .with_file(
            "/mock/home/.ccs/a.settings.json",
            r#"{"env":{"KEY":"value_a"}}"#,
        )
        .with_file(
            "/mock/home/.ccs/b.settings.json",
            r#"{"env":{"KEY":"value_b"}}"#,
        );

    let result_a = load_ccs_env_vars_with_deps(&env, &fs, "profile_a").unwrap();
    let result_b = load_ccs_env_vars_with_deps(&env, &fs, "profile_b").unwrap();

    assert_eq!(result_a.get("KEY").unwrap(), "value_a");
    assert_eq!(result_b.get("KEY").unwrap(), "value_b");
}
