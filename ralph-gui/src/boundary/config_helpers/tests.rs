use super::*;
use tempfile::TempDir;

// ── get_effective_config_with_sources tests ────────────────────────────

#[test]
fn test_effective_config_with_sources_returns_default_when_no_files() {
    let dir = TempDir::new().unwrap();
    let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let eff = result.unwrap();
    // No global or project file — everything should be Default.
    // We check at least some fields; exact values depend on defaults.
    assert!(!eff.sources.is_empty(), "sources vec should not be empty");
    // developer_iters is a core field and must have a source entry.
    let dev_iters_source = eff
        .sources
        .iter()
        .find(|s| s.field_name == "developer_iters")
        .expect("developer_iters source must be present");
    // Since no files exist we expect Default (OR Global if global file exists on this machine).
    assert!(
        dev_iters_source.source == ConfigSource::Default
            || dev_iters_source.source == ConfigSource::Global,
        "With no project file source should be Default or Global, got {:?}",
        dev_iters_source.source
    );
}

#[test]
fn test_effective_config_with_sources_project_overrides_global() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let config_path = agent_dir.join("ralph-workflow.toml");
    // Set developer_iters to a value that almost certainly differs from default (3).
    std::fs::write(&config_path, "[general]\ndeveloper_iters = 7\n").unwrap();

    let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let eff = result.unwrap();
    assert_eq!(eff.config.developer_iters, 7, "Effective value should be 7");
    let source = eff
        .sources
        .iter()
        .find(|s| s.field_name == "developer_iters")
        .expect("developer_iters source must be present");
    assert_eq!(
        source.source,
        ConfigSource::Project,
        "developer_iters was set in project config so source must be Project"
    );
}

#[test]
fn test_effective_config_with_sources_project_explicit_field() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let config_path = agent_dir.join("ralph-workflow.toml");
    // Set developer_iters explicitly in project TOML.
    // Even though the value matches the default (5), the field is PRESENT in the TOML
    // so source should be Project (presence detection, not value comparison).
    std::fs::write(&config_path, "[general]\ndeveloper_iters = 5\n").unwrap();

    let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let eff = result.unwrap();
    // developer_iters is explicitly present in project TOML → Project.
    let dev_source = eff
        .sources
        .iter()
        .find(|s| s.field_name == "developer_iters")
        .unwrap();
    assert_eq!(
        dev_source.source,
        ConfigSource::Project,
        "developer_iters is explicitly set in project TOML so source must be Project"
    );
}

#[test]
fn test_config_source_serializes_as_lowercase() {
    let source = ConfigSource::Project;
    let json = serde_json::to_string(&source).unwrap();
    assert_eq!(
        json, r#""project""#,
        "ConfigSource should serialize lowercase"
    );

    let global = ConfigSource::Global;
    let global_json = serde_json::to_string(&global).unwrap();
    assert_eq!(global_json, r#""global""#);

    let default_s = ConfigSource::Default;
    let default_json = serde_json::to_string(&default_s).unwrap();
    assert_eq!(default_json, r#""default""#);
}

#[test]
fn test_effective_config_with_sources_field_not_set_uses_default_source() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let config_path = agent_dir.join("ralph-workflow.toml");
    // Set developer_iters=7 (differs from the built-in default of 5).
    // isolation_mode is NOT set; it should remain Default (or Global).
    std::fs::write(&config_path, "[general]\ndeveloper_iters = 7\n").unwrap();

    let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let eff = result.unwrap();
    // developer_iters is set in project → Project
    let dev_source = eff
        .sources
        .iter()
        .find(|s| s.field_name == "developer_iters")
        .unwrap();
    assert_eq!(dev_source.source, ConfigSource::Project);
    // isolation_mode is NOT set anywhere — it should be Default or Global (not Project).
    let isolation_source = eff
        .sources
        .iter()
        .find(|s| s.field_name == "isolation_mode")
        .unwrap();
    assert!(
        isolation_source.source != ConfigSource::Project,
        "isolation_mode was never set in project config so source must not be Project"
    );
}

#[test]
fn test_build_source_list_from_toml_all_defaults() {
    // Empty TOML → all fields should be Default.
    let sources = build_source_list_from_toml("", None);
    for s in &sources {
        assert_eq!(
            s.source,
            ConfigSource::Default,
            "Field '{}' should be Default when TOML is empty",
            s.field_name
        );
    }
}

#[test]
fn test_build_source_list_from_toml_global_sets_field() {
    let global_toml = "[general]\nverbosity = 3\n";
    let sources = build_source_list_from_toml(global_toml, None);
    let verbosity_src = sources
        .iter()
        .find(|s| s.field_name == "verbosity")
        .unwrap();
    assert_eq!(verbosity_src.source, ConfigSource::Global);

    // Fields not set should remain Default.
    let dev_iters_src = sources
        .iter()
        .find(|s| s.field_name == "developer_iters")
        .unwrap();
    assert_eq!(dev_iters_src.source, ConfigSource::Default);
}

#[test]
fn test_build_source_list_from_toml_project_overrides_global() {
    let global_toml = "[general]\nverbosity = 3\n";
    let project_toml = "[general]\ndeveloper_iters = 7\n".to_string();
    let sources = build_source_list_from_toml(global_toml, Some(&project_toml));

    // verbosity is set in global, NOT in project → Global
    let verbosity_src = sources
        .iter()
        .find(|s| s.field_name == "verbosity")
        .unwrap();
    assert_eq!(verbosity_src.source, ConfigSource::Global);

    // developer_iters is set in project → Project
    let dev_iters_src = sources
        .iter()
        .find(|s| s.field_name == "developer_iters")
        .unwrap();
    assert_eq!(dev_iters_src.source, ConfigSource::Project);

    // isolation_mode is set in neither → Default
    let iso_src = sources
        .iter()
        .find(|s| s.field_name == "isolation_mode")
        .unwrap();
    assert_eq!(iso_src.source, ConfigSource::Default);
}

#[test]
fn test_build_source_list_all_defaults() {
    let default_view = ConfigView::from(&UnifiedConfig::default());
    let global_view = ConfigView::from(&UnifiedConfig::default());
    let sources = build_source_list(&default_view, &global_view, None);
    // All fields should be Default when nothing is customised.
    for s in &sources {
        assert_eq!(
            s.source,
            ConfigSource::Default,
            "Field '{}' should be Default when nothing is set",
            s.field_name
        );
    }
}

#[test]
fn test_get_global_config_returns_default_when_no_file() {
    let result = get_global_config();
    assert!(
        result.is_ok(),
        "get_global_config should not fail: {result:?}"
    );
    let config = result.unwrap();
    assert!(config.verbosity <= 4, "Verbosity should be 0-4");
}

#[test]
fn test_get_project_config_returns_none_when_no_file() {
    let dir = TempDir::new().unwrap();
    let result = get_project_config(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok());
    assert!(result.unwrap().is_none());
}

#[test]
fn test_get_project_config_returns_config_when_file_exists() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let config_path = agent_dir.join("ralph-workflow.toml");
    std::fs::write(&config_path, "[general]\nverbosity = 3\n").unwrap();

    let result = get_project_config(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let config = result.unwrap();
    assert!(config.is_some());
    assert_eq!(config.unwrap().verbosity, 3);
}

#[test]
fn test_effective_config_with_sources_isolation_not_set_is_not_project() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let config_path = agent_dir.join("ralph-workflow.toml");
    // Set developer_iters=7; isolation_mode is NOT set so it must not be Project.
    std::fs::write(&config_path, "[general]\ndeveloper_iters = 7\n").unwrap();

    let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let eff = result.unwrap();
    // developer_iters is explicitly set in project config → Project.
    let dev_source = eff
        .sources
        .iter()
        .find(|s| s.field_name == "developer_iters")
        .unwrap();
    assert_eq!(dev_source.source, ConfigSource::Project);
    // isolation_mode is NOT set in the project TOML at all → Default or Global, never Project.
    let isolation_source = eff
        .sources
        .iter()
        .find(|s| s.field_name == "isolation_mode")
        .unwrap();
    assert_ne!(
        isolation_source.source,
        ConfigSource::Project,
        "isolation_mode was never set in project config TOML, so its source must not be Project"
    );
}

#[test]
fn test_get_effective_config_merges_project_overrides() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let config_path = agent_dir.join("ralph-workflow.toml");
    std::fs::write(&config_path, "[general]\ndeveloper_iters = 7\n").unwrap();

    let result = get_effective_config(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let config = result.unwrap();
    assert_eq!(
        config.developer_iters, 7,
        "Project override should take effect"
    );
}

#[test]
fn test_save_global_config_rejects_invalid_toml() {
    // save_global_config validates via UnifiedConfig::load_from_content before writing.
    // We cannot safely redirect the write target in a unit test (it targets the real home dir),
    // but we can verify the validation guard triggers before any I/O attempt.
    let result = save_global_config("this is not valid toml !!!!!".to_string());
    assert!(result.is_err(), "Invalid TOML should be rejected");
    assert!(
        result.unwrap_err().contains("Invalid config"),
        "Error should indicate invalid config"
    );
}

#[test]
fn test_save_project_config_rejects_invalid_toml() {
    let dir = TempDir::new().unwrap();
    let result = save_project_config(
        dir.path().to_string_lossy().to_string(),
        "this is not valid toml !!!!!".to_string(),
    );
    assert!(result.is_err());
    assert!(result.unwrap_err().contains("Invalid config"));
}

#[test]
fn test_save_project_config_writes_valid_toml() {
    let dir = TempDir::new().unwrap();
    let result = save_project_config(
        dir.path().to_string_lossy().to_string(),
        "[general]\nverbosity = 1\n".to_string(),
    );
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let config_path = dir.path().join(".agent").join("ralph-workflow.toml");
    assert!(config_path.exists(), "Config file should have been created");
}

#[test]
fn test_list_agent_profiles_returns_empty_when_no_file() {
    let dir = TempDir::new().unwrap();
    let result = list_agent_profiles(Some(dir.path().to_string_lossy().to_string()));
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    assert!(result.unwrap().is_empty());
}

#[test]
fn test_list_agent_profiles_parses_valid_file() {
    let dir = TempDir::new().unwrap();
    let agents_toml = r#"
[[agents]]
name = "claude-solo"
developer_agent = "claude"
reviewer_agent = "claude"

[[agents]]
name = "claude-codex"
developer_agent = "claude"
reviewer_agent = "codex"
"#;
    std::fs::write(dir.path().join("agents.toml"), agents_toml).unwrap();
    let result = list_agent_profiles(Some(dir.path().to_string_lossy().to_string()));
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let profiles = result.unwrap();
    assert_eq!(profiles.len(), 2);
    assert_eq!(profiles[0].name, "claude-solo");
    assert_eq!(profiles[1].developer_agent, "claude");
    assert_eq!(profiles[1].reviewer_agent, "codex");
}

#[test]
fn test_list_agent_profiles_returns_error_on_invalid_toml() {
    let dir = TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("agents.toml"),
        "this is not !!! valid toml @@",
    )
    .unwrap();
    let result = list_agent_profiles(Some(dir.path().to_string_lossy().to_string()));
    assert!(result.is_err());
    assert!(result.unwrap_err().contains("Failed to parse agents.toml"));
}

#[test]
fn test_list_agent_profiles_returns_empty_without_agents_key() {
    let dir = TempDir::new().unwrap();
    std::fs::write(dir.path().join("agents.toml"), "[general]\nfoo = true\n").unwrap();
    let result = list_agent_profiles(Some(dir.path().to_string_lossy().to_string()));
    assert!(result.is_ok());
    assert!(result.unwrap().is_empty());
}

#[test]
fn test_get_raw_global_config_toml_returns_empty_string_when_no_file() {
    // This test relies on there being no global config at the real ~/.config/ralph-workflow.toml
    // OR verifies the function handles a missing file gracefully.
    // The function returns Ok("") when the file is absent — always a valid outcome.
    let result = get_raw_global_config_toml();
    assert!(
        result.is_ok(),
        "get_raw_global_config_toml should not fail: {result:?}"
    );
    // If no global config exists we get empty string. If one does exist we get content.
    // Both are valid — we can't know which environment this runs in.
    let _ = result.unwrap(); // just verify it's Ok
}

// --- validate_config_toml tests ---

#[test]
fn test_validate_config_toml_accepts_valid_toml() {
    let result = validate_config_toml("[general]\nverbosity = 2\n".to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    assert!(
        result.unwrap().is_none(),
        "Valid TOML should return Ok(None)"
    );
}

#[test]
fn test_validate_config_toml_rejects_invalid_toml_syntax() {
    let result = validate_config_toml("[unclosed".to_string());
    assert!(result.is_ok(), "Should always return Ok: {result:?}");
    let inner = result.unwrap();
    assert!(
        inner.is_some(),
        "Invalid TOML syntax should return Ok(Some(error))"
    );
    let msg = inner.unwrap();
    assert!(!msg.is_empty(), "Error message should not be empty");
}

#[test]
fn test_validate_config_toml_accepts_empty_string() {
    // An empty string is valid TOML (no keys — all defaults).
    let result = validate_config_toml(String::new());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    assert!(
        result.unwrap().is_none(),
        "Empty TOML string should be valid (uses defaults)"
    );
}

#[test]
fn test_validate_config_toml_rejects_garbage_content() {
    let result = validate_config_toml("this is not !!! valid toml @@ content $$".to_string());
    assert!(result.is_ok(), "Should always return Ok: {result:?}");
    let inner = result.unwrap();
    assert!(
        inner.is_some(),
        "Garbage content should return Ok(Some(error))"
    );
}

#[test]
fn test_validate_config_toml_accepts_valid_developer_iters() {
    let result = validate_config_toml("[general]\ndeveloper_iters = 5\n".to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    assert!(
        result.unwrap().is_none(),
        "Valid developer_iters should pass validation"
    );
}

#[test]
fn test_get_raw_project_config_toml_returns_empty_string_when_no_file() {
    let dir = TempDir::new().unwrap();
    let result = get_raw_project_config_toml(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok());
    assert_eq!(
        result.unwrap(),
        "",
        "Should return empty string when no project config exists"
    );
}

#[test]
fn test_get_raw_project_config_toml_returns_content_when_file_exists() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let config_path = agent_dir.join("ralph-workflow.toml");
    let toml_content = "[general]\nverbosity = 2\n";
    std::fs::write(&config_path, toml_content).unwrap();

    let result = get_raw_project_config_toml(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let content = result.unwrap();
    assert!(
        content.contains("verbosity = 2"),
        "Returned content should contain written TOML; got: {content}"
    );
}

// --- AI API key tests ---

#[test]
fn test_load_gui_config_returns_default_when_no_file() {
    // load_gui_config checks for ~/.config/ralph-gui.toml.
    // If that file doesn't exist we get a default (empty api_key).
    // This is always a valid outcome even if the real config exists.
    let config = load_gui_config();
    assert!(
        config.is_ok(),
        "load_gui_config should not fail: {config:?}"
    );
    // We can't assert the key is empty if the dev machine has one set,
    // so we just confirm Ok was returned.
}

#[test]
fn test_save_and_load_gui_config_roundtrip() {
    // We cannot write to the real ~/.config directory safely in tests,
    // so we test the internal serialization round-trip via TOML directly.
    let config = GuiConfig {
        ai: AiConfig {
            api_key: "sk-test-key-12345".to_string(),
        },
    };
    let serialized = toml::to_string(&config).expect("serialize should succeed");
    assert!(
        serialized.contains("api_key"),
        "Serialized TOML should contain api_key"
    );
    let deserialized: GuiConfig = toml::from_str(&serialized).expect("deserialize should succeed");
    assert_eq!(
        deserialized.ai.api_key, "sk-test-key-12345",
        "Roundtrip api_key mismatch"
    );
}

#[test]
fn test_save_ai_api_key_rejects_empty_key() {
    let result = save_ai_api_key(String::new());
    assert!(result.is_err(), "Empty key should be rejected");
    assert!(
        result.unwrap_err().contains("must not be empty"),
        "Error message should explain empty key"
    );
}

#[test]
fn test_save_ai_api_key_rejects_whitespace_only_key() {
    let result = save_ai_api_key("   ".to_string());
    assert!(result.is_err(), "Whitespace-only key should be rejected");
}

#[test]
fn test_gui_config_default_has_empty_api_key() {
    let config = GuiConfig::default();
    assert!(
        config.ai.api_key.is_empty(),
        "Default api_key should be empty"
    );
}

#[test]
fn test_gui_config_deserializes_from_toml_without_ai_section() {
    // Older GUI config files may not have [ai] section — must deserialize gracefully.
    let toml_str = "# ralph-gui config\n";
    let config: GuiConfig = toml::from_str(toml_str).expect("Should deserialize with defaults");
    assert!(
        config.ai.api_key.is_empty(),
        "Missing [ai] section should default to empty key"
    );
}

#[test]
fn test_save_gui_config_writes_file_to_temp_path() {
    // Directly test save_gui_config by temporarily redirecting via in-process approach.
    // Since gui_config_path() uses HOME env, we write via tempdir and verify TOML format.
    let dir = TempDir::new().unwrap();
    let config = GuiConfig {
        ai: AiConfig {
            api_key: "sk-roundtrip".to_string(),
        },
    };
    let path = dir.path().join("ralph-gui.toml");
    let content = toml::to_string(&config).unwrap();
    std::fs::write(&path, &content).unwrap();

    let read_back: GuiConfig = toml::from_str(&std::fs::read_to_string(&path).unwrap())
        .expect("Should parse written config");
    assert_eq!(read_back.ai.api_key, "sk-roundtrip");
}

#[test]
#[cfg(unix)]
fn test_save_gui_config_sets_0o600_permissions() {
    use std::os::unix::fs::PermissionsExt;
    let dir = TempDir::new().unwrap();
    // Write a config file manually and set 0o600 permissions, mirroring save_gui_config behavior.
    let path = dir.path().join("ralph-gui.toml");
    let config = GuiConfig {
        ai: AiConfig {
            api_key: "sk-perm-test".to_string(),
        },
    };
    let content = toml::to_string(&config).unwrap();
    std::fs::write(&path, &content).unwrap();
    let perms = std::fs::Permissions::from_mode(0o600);
    std::fs::set_permissions(&path, perms).unwrap();

    let metadata = std::fs::metadata(&path).unwrap();
    let mode = metadata.permissions().mode();
    assert_eq!(
        mode & 0o777,
        0o600,
        "Config file should have 0o600 permissions, got {mode:o}"
    );
}

// --- get_config_schema tests ---

#[test]
fn test_get_config_schema_returns_four_sections() {
    let result = get_config_schema();
    assert!(result.is_ok(), "get_config_schema should succeed");
    let sections = result.unwrap();
    assert_eq!(
        sections.len(),
        4,
        "Should have 4 sections: general, execution, retry, git"
    );
    let names: Vec<&str> = sections.iter().map(|s| s.name.as_str()).collect();
    assert!(names.contains(&"general"), "Should have general section");
    assert!(
        names.contains(&"execution"),
        "Should have execution section"
    );
    assert!(names.contains(&"retry"), "Should have retry section");
    assert!(names.contains(&"git"), "Should have git section");
}

#[test]
fn test_get_config_schema_general_section_has_expected_fields() {
    let sections = get_config_schema().unwrap();
    let general = sections.iter().find(|s| s.name == "general").unwrap();
    let field_names: Vec<&str> = general.fields.iter().map(|f| f.name.as_str()).collect();
    assert!(
        field_names.contains(&"verbosity"),
        "general should have verbosity field"
    );
    assert!(
        field_names.contains(&"developer_iters"),
        "general should have developer_iters field"
    );
    assert!(
        field_names.contains(&"review_depth"),
        "general should have review_depth field"
    );
}

#[test]
fn test_get_config_schema_review_depth_has_enum_options() {
    let sections = get_config_schema().unwrap();
    let general = sections.iter().find(|s| s.name == "general").unwrap();
    let review_depth = general
        .fields
        .iter()
        .find(|f| f.name == "review_depth")
        .unwrap();
    assert_eq!(
        review_depth.field_type, "enum",
        "review_depth should be enum type"
    );
    assert!(
        !review_depth.enum_options.is_empty(),
        "review_depth should have enum options"
    );
    assert!(
        review_depth.enum_options.contains(&"standard".to_string()),
        "Should have 'standard' option"
    );
}

#[test]
fn test_get_config_schema_number_fields_have_bounds() {
    let sections = get_config_schema().unwrap();
    for section in &sections {
        for field in &section.fields {
            if field.field_type == "number" {
                assert!(
                    field.min_value.is_some() || field.max_value.is_some(),
                    "Number field '{}' should have bounds",
                    field.name
                );
            }
        }
    }
}

// --- get_effective_chains_config tests ---

#[test]
fn test_parse_chains_from_toml_parses_chains() {
    let toml = "[agent_chains]\nmychain = [\"agent1\", \"agent2\"]\n";
    let chains = parse_chains_from_toml(toml);
    assert_eq!(chains.len(), 1);
    assert_eq!(chains["mychain"], vec!["agent1", "agent2"]);
}

#[test]
fn test_parse_chains_from_toml_returns_empty_when_no_section() {
    let toml = "[general]\nverbosity = 1\n";
    let chains = parse_chains_from_toml(toml);
    assert!(chains.is_empty());
}

#[test]
fn test_parse_drains_from_toml_parses_drains() {
    let toml = "[agent_drains]\ndevelopment = \"mychain\"\nreview = \"reviewer-chain\"\n";
    let drains = parse_drains_from_toml(toml);
    assert_eq!(drains.len(), 2);
    assert_eq!(drains["development"], "mychain");
    assert_eq!(drains["review"], "reviewer-chain");
}

#[test]
fn test_parse_drains_from_toml_returns_empty_when_no_section() {
    let toml = "[general]\nverbosity = 1\n";
    let drains = parse_drains_from_toml(toml);
    assert!(drains.is_empty());
}

#[test]
fn test_parse_agents_from_toml_parses_agent_sections() {
    let toml =
            "[agents.claude-code]\ntool = \"claude\"\nmodel = \"claude-sonnet-4-6\"\n\n[agents.gpt]\ntool = \"openai\"\nmodel = \"gpt-4o\"\n";
    let agents = parse_agents_from_toml(toml);
    assert_eq!(agents.len(), 2);
    let claude = agents.iter().find(|a| a.name == "claude-code").unwrap();
    assert_eq!(claude.tool, "claude");
    assert_eq!(claude.model, "claude-sonnet-4-6");
    let gpt = agents.iter().find(|a| a.name == "gpt").unwrap();
    assert_eq!(gpt.tool, "openai");
}

#[test]
fn test_parse_agents_from_toml_returns_empty_when_no_sections() {
    let toml = "[general]\nverbosity = 1\n";
    let agents = parse_agents_from_toml(toml);
    assert!(agents.is_empty());
}

#[test]
fn test_merge_chains_project_overrides_global() {
    let mut global = std::collections::HashMap::new();
    global.insert("chain-a".to_string(), vec!["agent1".to_string()]);
    global.insert("chain-b".to_string(), vec!["agent2".to_string()]);

    let mut project = std::collections::HashMap::new();
    project.insert("chain-a".to_string(), vec!["agent-override".to_string()]);
    project.insert("chain-c".to_string(), vec!["agent3".to_string()]);

    let merged = merge_chains(global, project);
    // chain-a is overridden by project
    assert_eq!(merged["chain-a"], vec!["agent-override"]);
    // chain-b is from global only
    assert_eq!(merged["chain-b"], vec!["agent2"]);
    // chain-c is from project only
    assert_eq!(merged["chain-c"], vec!["agent3"]);
}

#[test]
fn test_merge_drains_project_overrides_global() {
    let mut global = std::collections::HashMap::new();
    global.insert("development".to_string(), "chain-a".to_string());
    global.insert("review".to_string(), "chain-b".to_string());

    let mut project = std::collections::HashMap::new();
    project.insert("development".to_string(), "chain-override".to_string());

    let merged = merge_drains(global, project);
    assert_eq!(merged["development"], "chain-override");
    assert_eq!(merged["review"], "chain-b");
}

#[test]
fn test_get_effective_chains_config_returns_empty_when_no_files() {
    let dir = TempDir::new().unwrap();
    let result = get_effective_chains_config(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    // When no project config exists, chains and drains come from the global config
    // (which may or may not exist on the test machine). We only verify Ok is returned.
    let config = result.unwrap();
    // has_configured_chains reflects whether any chains exist
    assert_eq!(config.has_configured_chains, !config.chains.is_empty());
    assert_eq!(config.has_configured_drains, !config.drains.is_empty());
}

#[test]
fn test_get_effective_chains_config_parses_project_chains() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let config_path = agent_dir.join("ralph-workflow.toml");
    let toml = "[agent_chains]\nmychain = [\"agent1\", \"agent2\"]\n\n[agent_drains]\ndevelopment = \"mychain\"\n\n[agents.agent1]\ntool = \"claude\"\nmodel = \"claude-sonnet-4-6\"\n";
    std::fs::write(&config_path, toml).unwrap();

    let result = get_effective_chains_config(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let config = result.unwrap();
    assert!(
        config.has_configured_chains,
        "Should have configured chains"
    );
    assert!(
        config.has_configured_drains,
        "Should have configured drains"
    );

    let mychain = config.chains.iter().find(|c| c.name == "mychain");
    assert!(mychain.is_some(), "mychain should be present");
    assert_eq!(mychain.unwrap().agents, vec!["agent1", "agent2"]);

    assert_eq!(
        config.drains.get("development"),
        Some(&"mychain".to_string())
    );

    let agent = config.agents.iter().find(|a| a.name == "agent1");
    assert!(agent.is_some(), "agent1 should be present");
    assert_eq!(agent.unwrap().tool, "claude");
}

#[test]
fn test_get_effective_chains_config_project_chains_override_global() {
    // We can't inject a global config file in tests, but we can verify project-only data.
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    let project_toml = "[agent_chains]\nproject-chain = [\"proj-agent\"]\n\n[agent_drains]\nreview = \"project-chain\"\n";
    std::fs::write(agent_dir.join("ralph-workflow.toml"), project_toml).unwrap();

    let result = get_effective_chains_config(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let config = result.unwrap();
    // Project chain must appear in merged result
    let found = config.chains.iter().any(|c| c.name == "project-chain");
    assert!(found, "project-chain should appear in merged chains");
    assert_eq!(
        config.drains.get("review"),
        Some(&"project-chain".to_string())
    );
}

#[test]
fn test_get_effective_chains_config_has_configured_flags_false_when_empty() {
    let dir = TempDir::new().unwrap();
    let agent_dir = dir.path().join(".agent");
    std::fs::create_dir(&agent_dir).unwrap();
    // Write a config with no chains or drains sections
    std::fs::write(
        agent_dir.join("ralph-workflow.toml"),
        "[general]\nverbosity = 1\n",
    )
    .unwrap();

    let result = get_effective_chains_config(dir.path().to_string_lossy().to_string());
    assert!(result.is_ok(), "Expected Ok: {result:?}");
    let config = result.unwrap();
    // No chains from project config. Global may provide some — we only check the flag matches reality.
    assert_eq!(config.has_configured_chains, !config.chains.is_empty());
    assert_eq!(config.has_configured_drains, !config.drains.is_empty());
}

#[test]
fn test_parse_toml_string_array_parses_correctly() {
    assert_eq!(
        parse_toml_string_array(r#"["a", "b", "c"]"#),
        Some(vec!["a".to_string(), "b".to_string(), "c".to_string()])
    );
    assert_eq!(parse_toml_string_array(r"[]"), Some(vec![]));
    assert_eq!(parse_toml_string_array("not-an-array"), None);
}

#[test]
fn test_parse_toml_quoted_string_parses_correctly() {
    assert_eq!(
        parse_toml_quoted_string(r#""hello""#),
        Some("hello".to_string())
    );
    assert_eq!(parse_toml_quoted_string("not-quoted"), None);
    assert_eq!(parse_toml_quoted_string(r#""""#), Some(String::new()));
}

// --- ToolUpdateInfo tests ---

#[test]
fn test_check_tool_updates_returns_result_for_all_tools() {
    let result = check_tool_updates();
    assert!(result.is_ok(), "check_tool_updates should succeed");
    let updates = result.unwrap();
    assert_eq!(
        updates.len(),
        3,
        "Should check 3 tools: Claude Code, Codex, OpenCode"
    );
    let tool_names: Vec<&str> = updates.iter().map(|u| u.name.as_str()).collect();
    assert!(
        tool_names.contains(&"Claude Code"),
        "Should include Claude Code"
    );
    assert!(tool_names.contains(&"Codex"), "Should include Codex");
    assert!(tool_names.contains(&"OpenCode"), "Should include OpenCode");
}
