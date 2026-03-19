use crate::agents::AgentRegistry;
use crate::config::{CcsConfig, Config};
use crate::validation::validate_can_commit;
use std::collections::HashMap;
use std::path::Path;

#[test]
fn validate_can_commit_uses_resolve_config_for_ccs_refs() {
    let mut registry = AgentRegistry::new().unwrap();
    let defaults = CcsConfig {
        can_commit: false,
        ..CcsConfig::default()
    };
    registry.set_ccs_aliases(&HashMap::new(), defaults);

    let config = Config {
        developer_cmd: None,
        reviewer_cmd: None,
        ..Config::default()
    };

    let err = validate_can_commit(
        &config,
        &registry,
        "ccs/random",
        "claude",
        Path::new("ralph-workflow.toml"),
    )
    .unwrap_err();
    assert!(err.to_string().contains("can_commit=false"));
}
