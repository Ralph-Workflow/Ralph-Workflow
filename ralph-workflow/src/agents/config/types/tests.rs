use super::*;

#[test]
fn build_cmd_includes_expected_flags() {
    let agent = AgentConfig {
        cmd: "testbot run".to_string(),
        output_flag: "--json".to_string(),
        yolo_flag: "--yes".to_string(),
        verbose_flag: "--verbose".to_string(),
        can_commit: true,
        json_parser: JsonParserType::Generic,
        model_flag: None,
        print_flag: String::new(),
        streaming_flag: String::new(),
        session_flag: String::new(),
        env_vars: HashMap::new(),
        display_name: None,
    };

    let cmd = agent.build_cmd(true, true, true);
    assert!(cmd.contains("testbot run"));
    assert!(cmd.contains("--json"));
    assert!(cmd.contains("--yes"));
    assert!(cmd.contains("--verbose"));
}

#[test]
fn config_from_toml_sets_expected_fields() {
    let toml = AgentConfigToml {
        cmd: "myagent run".to_string(),
        output_flag: "--json".to_string(),
        yolo_flag: "--auto".to_string(),
        verbose_flag: "--verbose".to_string(),
        can_commit: false,
        json_parser: "claude".to_string(),
        model_flag: Some("-m provider/model".to_string()),
        print_flag: String::new(),
        streaming_flag: String::new(),
        session_flag: "--session {}".to_string(),
        ccs_profile: None,
        env_vars: HashMap::new(),
        display_name: Some("My Custom Agent".to_string()),
    };

    let config: AgentConfig = AgentConfig::from(toml);
    assert_eq!(config.cmd, "myagent run");
    assert!(!config.can_commit);
    assert_eq!(config.json_parser, JsonParserType::Claude);
    assert_eq!(config.model_flag, Some("-m provider/model".to_string()));
    assert_eq!(config.display_name, Some("My Custom Agent".to_string()));
    assert_eq!(config.session_flag, "--session {}");
}

#[test]
fn toml_defaults_are_applied() {
    let toml_str = r#"cmd = "myagent""#;
    let config: AgentConfigToml = toml::from_str(toml_str).unwrap();

    assert_eq!(config.cmd, "myagent");
    assert_eq!(config.output_flag, "");
    assert!(config.can_commit);
    assert_eq!(config.streaming_flag, "--include-partial-messages");
}

#[test]
fn build_cmd_includes_streaming_flag_with_print_and_stream_json() {
    let agent = AgentConfig {
        cmd: "ccs glm".to_string(),
        output_flag: "--output-format=stream-json".to_string(),
        yolo_flag: "--dangerously-skip-permissions".to_string(),
        verbose_flag: "--verbose".to_string(),
        can_commit: true,
        json_parser: JsonParserType::Claude,
        model_flag: None,
        print_flag: "--print".to_string(),
        streaming_flag: "--include-partial-messages".to_string(),
        session_flag: String::new(),
        env_vars: HashMap::new(),
        display_name: None,
    };

    let cmd = agent.build_cmd(true, true, true);
    assert!(cmd.contains("ccs glm --print"));
    assert!(cmd.contains("--output-format=stream-json"));
    assert!(cmd.contains("--include-partial-messages"));
}

#[test]
fn build_cmd_includes_streaming_flag_with_space_separated_stream_json() {
    let agent = AgentConfig {
        cmd: "claude".to_string(),
        output_flag: "--output-format stream-json".to_string(),
        yolo_flag: String::new(),
        verbose_flag: "--verbose".to_string(),
        can_commit: true,
        json_parser: JsonParserType::Claude,
        model_flag: None,
        print_flag: "--print".to_string(),
        streaming_flag: "--include-partial-messages".to_string(),
        session_flag: String::new(),
        env_vars: HashMap::new(),
        display_name: None,
    };

    let cmd = agent.build_cmd(true, false, false);
    assert!(cmd.contains("claude --print"));
    assert!(cmd.contains("--output-format stream-json"));
    assert!(cmd.contains("--include-partial-messages"));
}

#[test]
fn requires_verbose_for_json_handles_space_separated_stream_json() {
    let agent = AgentConfig {
        cmd: "claude".to_string(),
        output_flag: "--output-format stream-json".to_string(),
        yolo_flag: String::new(),
        verbose_flag: "--verbose".to_string(),
        can_commit: true,
        json_parser: JsonParserType::Claude,
        model_flag: None,
        print_flag: "--print".to_string(),
        streaming_flag: "--include-partial-messages".to_string(),
        session_flag: String::new(),
        env_vars: HashMap::new(),
        display_name: None,
    };

    let cmd = agent.build_cmd(true, false, false);
    assert!(cmd.contains("--verbose"));
}

#[test]
fn default_agents_toml_is_valid() {
    let registry = crate::agents::AgentRegistry::new().expect("default template should parse");
    assert!(registry.resolve_config("claude").is_some());
    assert!(registry.resolve_config("codex").is_some());
}

#[test]
fn build_cmd_with_session_includes_session_flag_when_supported() {
    let agent = AgentConfig {
        cmd: "opencode run".to_string(),
        output_flag: "--json".to_string(),
        yolo_flag: "--yes".to_string(),
        verbose_flag: "--verbose".to_string(),
        can_commit: true,
        json_parser: JsonParserType::OpenCode,
        model_flag: None,
        print_flag: String::new(),
        streaming_flag: String::new(),
        session_flag: "-s {}".to_string(),
        env_vars: HashMap::new(),
        display_name: None,
    };

    let cmd = agent.build_cmd_with_session(true, true, true, None, None);
    assert!(!cmd.contains("-s "));

    let cmd = agent.build_cmd_with_session(true, true, true, None, Some("ses_abc123"));
    assert!(cmd.contains("-s ses_abc123"));
}

#[test]
fn build_cmd_with_session_ignores_session_id_when_unsupported() {
    let agent = AgentConfig {
        cmd: "generic-agent".to_string(),
        output_flag: String::new(),
        yolo_flag: String::new(),
        verbose_flag: String::new(),
        can_commit: true,
        json_parser: JsonParserType::Generic,
        model_flag: None,
        print_flag: String::new(),
        streaming_flag: String::new(),
        session_flag: String::new(),
        env_vars: HashMap::new(),
        display_name: None,
    };

    let cmd = agent.build_cmd_with_session(false, false, false, None, Some("ses_abc123"));
    assert!(!cmd.contains("ses_abc123"));
    assert!(!agent.supports_session_continuation());
}
