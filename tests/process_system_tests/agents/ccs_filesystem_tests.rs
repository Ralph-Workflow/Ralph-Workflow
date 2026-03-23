// Process system tests for CCS binary discovery that require real filesystem operations
// These tests cannot use MemoryWorkspace as they need to interact with PATH and real executables
//
// IMPORTANT: `#[serial]` is NOT used here. Each test that modifies process-global
// state (PATH, CCS_HOME) must acquire the module-local `ENV_LOCK` Mutex for the
// duration of the mutation. This provides fine-grained intra-module serialization
// without forcing the entire test binary to run serially.
//
// If you need to add a test that modifies process-global state, acquire `ENV_LOCK`
// before mutating — do NOT introduce `#[serial]`.

use ralph_workflow::agents::ccs::{
    build_ccs_agent_config, ccs_env_var_debug_summary, resolve_ccs_command,
};
use ralph_workflow::config::{CcsAliasConfig, CcsConfig};
use std::sync::Mutex;

// NOTE: Some tests in this file need to temporarily modify process-wide env vars
// (PATH/CCS_HOME). Guard them with a mutex to reduce cross-test interference.
static ENV_LOCK: Mutex<()> = Mutex::new(());

struct EnvVarGuard {
    key: &'static str,
    old: Option<String>,
}

impl EnvVarGuard {
    fn set(key: &'static str, value: &str) -> Self {
        let old = std::env::var(key).ok();
        std::env::set_var(key, value);
        Self { key, old }
    }
}

impl Drop for EnvVarGuard {
    fn drop(&mut self) {
        if let Some(old) = &self.old {
            std::env::set_var(self.key, old);
        } else {
            std::env::remove_var(self.key);
        }
    }
}

fn make_temp_dir(prefix: &str) -> std::path::PathBuf {
    let mut dir = std::env::temp_dir();
    let pid = std::process::id();
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    dir.push(format!("ralph-ccs-tests-{prefix}-{pid}-{nanos}"));
    std::fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

fn install_fake_claude_on_path() -> (std::path::PathBuf, EnvVarGuard) {
    let bin_dir = make_temp_dir("bin");

    #[cfg(windows)]
    let claude_path = bin_dir.join("claude.cmd");
    #[cfg(not(windows))]
    let claude_path = bin_dir.join("claude");

    #[cfg(windows)]
    {
        std::fs::write(&claude_path, "@echo off\recho claude\r\n").expect("write fake claude");
    }

    #[cfg(not(windows))]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::write(&claude_path, "#!/bin/sh\necho claude\n").expect("write fake claude");
        let mut perms = std::fs::metadata(&claude_path)
            .expect("stat fake claude")
            .permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&claude_path, perms).expect("chmod fake claude");
    }

    // Prepend our bin dir to PATH (avoid clobbering the existing PATH).
    let old_path = std::env::var("PATH").unwrap_or_default();
    let new_path = format!("{}:{}", bin_dir.display(), old_path);
    let guard = EnvVarGuard::set("PATH", &new_path);

    (claude_path, guard)
}

fn default_ccs() -> CcsConfig {
    CcsConfig::default()
}

#[test]
fn test_non_glm_never_bypasses_ccs_wrapper_even_if_env_vars_loaded() {
    let _lock = ENV_LOCK.lock().unwrap();
    let (_claude_path, _path_guard) = install_fake_claude_on_path();

    let alias_config = CcsAliasConfig {
        cmd: "ccs gemini".to_string(),
        ..CcsAliasConfig::default()
    };

    // Simulate: env vars were loaded, and claude exists on PATH.
    // Desired behavior: only GLM is allowed to bypass; everything else must run `ccs ...`.
    let resolved = resolve_ccs_command(&alias_config, "gemini", true, None, false);
    assert_eq!(resolved.command, "ccs gemini");
}

#[test]
fn test_glm_can_bypass_ccs_wrapper_when_env_vars_loaded() {
    let _lock = ENV_LOCK.lock().unwrap();
    let (claude_path, _path_guard) = install_fake_claude_on_path();

    let alias_config = CcsAliasConfig {
        cmd: "ccs glm".to_string(),
        ..CcsAliasConfig::default()
    };

    let resolved = resolve_ccs_command(&alias_config, "glm", true, None, false);
    assert_eq!(resolved.command, claude_path.to_string_lossy().to_string());
}

#[test]
fn test_build_ccs_agent_config_skips_env_var_loading_for_non_glm() {
    let _lock = ENV_LOCK.lock().unwrap();
    let (_claude_path, _path_guard) = install_fake_claude_on_path();

    // Create a fake CCS_HOME with a working gemini profile env var file.
    let home = make_temp_dir("home");
    let ccs_dir = home.join(".ccs");
    std::fs::create_dir_all(&ccs_dir).expect("create .ccs dir");
    std::fs::write(
        ccs_dir.join("config.json"),
        r#"{"profiles":{"gemini":"gemini.settings.json"}}"#,
    )
    .expect("write config.json");
    std::fs::write(
        ccs_dir.join("gemini.settings.json"),
        r#"{"env":{"ANTHROPIC_BASE_URL":"https://example","ANTHROPIC_AUTH_TOKEN":"tok"}}"#,
    )
    .expect("write settings");

    let _ccs_home_guard = EnvVarGuard::set("CCS_HOME", home.to_string_lossy().as_ref());

    let config = build_ccs_agent_config(
        &CcsAliasConfig {
            cmd: "ccs gemini".to_string(),
            ..CcsAliasConfig::default()
        },
        &default_ccs(),
        "ccs-gemini".to_string(),
        "gemini",
    );

    // Non-GLM aliases must not use GLM-style CCS env var injection.
    assert_eq!(config.cmd, "ccs gemini");
    assert!(config.env_vars.is_empty());
}

#[test]
fn test_build_ccs_agent_config_loads_env_vars_for_glm() {
    let _lock = ENV_LOCK.lock().unwrap();
    let (claude_path, _path_guard) = install_fake_claude_on_path();

    // Create a fake CCS_HOME with a working glm profile env var file.
    let home = make_temp_dir("home2");
    let ccs_dir = home.join(".ccs");
    std::fs::create_dir_all(&ccs_dir).expect("create .ccs dir");
    std::fs::write(
        ccs_dir.join("config.json"),
        r#"{"profiles":{"glm":"glm.settings.json"}}"#,
    )
    .expect("write config.json");
    std::fs::write(
        ccs_dir.join("glm.settings.json"),
        r#"{"env":{"ANTHROPIC_BASE_URL":"https://api.example","ANTHROPIC_AUTH_TOKEN":"tok","ANTHROPIC_MODEL":"glm-4.7","CUSTOM_ENV":"value"}}"#,
    )
    .expect("write settings");

    let _ccs_home_guard = EnvVarGuard::set("CCS_HOME", home.to_string_lossy().as_ref());

    let config = build_ccs_agent_config(
        &CcsAliasConfig {
            cmd: "ccs glm".to_string(),
            ..CcsAliasConfig::default()
        },
        &default_ccs(),
        "ccs-glm".to_string(),
        "glm",
    );

    assert_eq!(config.cmd, claude_path.to_string_lossy().to_string());
    assert!(config.env_vars.contains_key("ANTHROPIC_MODEL"));

    // Verify environment variable classification by checking specific keys and counts match
    // expectations from the test env vars. Per integration testing guide: combine length
    // assertions with content checks.
    let summary = ccs_env_var_debug_summary(&config.env_vars);

    // Verify environment variable classification:
    // - ANTHROPIC_MODEL and ANTHROPIC_BASE_URL should be whitelisted (safe to log)
    // - ANTHROPIC_AUTH_TOKEN should be redacted (sensitive credential)
    // - CUSTOM_ENV should be hidden (not whitelisted, not classified as sensitive)
    assert!(
        summary
            .whitelisted_keys_present
            .contains(&"ANTHROPIC_MODEL".to_string()),
        "Expected ANTHROPIC_MODEL to be whitelisted"
    );

    assert!(
        summary
            .whitelisted_keys_present
            .contains(&"ANTHROPIC_BASE_URL".to_string()),
        "Expected ANTHROPIC_BASE_URL to be whitelisted"
    );

    assert_eq!(
        summary.redacted_sensitive_keys, 1,
        "Expected 1 redacted sensitive key (ANTHROPIC_AUTH_TOKEN)"
    );

    assert_eq!(
        summary.hidden_non_whitelisted_keys, 1,
        "Expected 1 hidden non-whitelisted key (CUSTOM_ENV)"
    );
}
