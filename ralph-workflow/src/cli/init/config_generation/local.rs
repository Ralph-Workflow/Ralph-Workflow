//! Local configuration file creation.
//!
//! Handles `--init-local-config` flag to create a local config file at
//! `.agent/ralph-workflow.toml` in the current directory.
//!
//! The generated template shows the user's current effective values
//! (from global config or built-in defaults) as commented-out entries,
//! so they know what they can override.

use crate::agents::AgentRegistry;
use crate::config::unified::UnifiedConfig;
use crate::config::{ConfigEnvironment, RealConfigEnvironment};
use crate::logger::Colors;
use std::collections::BTreeMap;
use std::fmt::Write;

/// Generate a local config template populated with effective values.
///
/// Reads the global config (if available) and falls back to built-in defaults
/// for any missing values. All values are shown as commented-out entries so
/// users can selectively uncomment and override only what they need.
fn generate_local_config_template<R: ConfigEnvironment>(env: &R) -> anyhow::Result<String> {
    generate_local_config_template_with(env, built_in_default_drains)
}

fn generate_local_config_template_with<R, F>(
    env: &R,
    default_drain_loader: F,
) -> anyhow::Result<String>
where
    R: ConfigEnvironment,
    F: FnOnce() -> anyhow::Result<crate::agents::fallback::ResolvedDrainConfig>,
{
    let effective = resolve_effective_init_template_config(env)?;
    let default_drains = default_drain_loader()?;

    let general = &effective.general;
    let resolved_drains = resolve_template_drains(&effective, &default_drains)?;
    let chain_definitions = collect_named_chain_definitions(&effective, &resolved_drains);
    let rendered_chain_definitions = chain_definitions
        .iter()
        .map(|(name, agents)| format!("# {name} = {}", format_toml_string_array(agents)))
        .collect::<Vec<_>>()
        .join("\n");
    let rendered_drain_bindings = crate::agents::AgentDrain::all()
        .into_iter()
        .map(|drain| {
            let binding = resolved_drains
                .binding(drain)
                .expect("built-in drain bindings should be fully resolved");
            format!("# {} = \"{}\"", drain.as_str(), binding.chain_name)
        })
        .collect::<Vec<_>>()
        .join("\n");

    let mut template = String::new();
    writeln!(
        template,
        "# Local Ralph configuration (.agent/ralph-workflow.toml)"
    )?;
    writeln!(
        template,
        "# Overrides ~/.config/ralph-workflow.toml for this project."
    )?;
    writeln!(template, "# Only uncomment settings you want to override.")?;
    writeln!(
        template,
        "# Run `ralph --check-config` to validate and see effective settings."
    )?;
    writeln!(template)?;
    writeln!(template, "[general]")?;
    writeln!(template, "# Project-specific iteration limits")?;
    writeln!(template, "# developer_iters = {}", general.developer_iters)?;
    writeln!(
        template,
        "# reviewer_reviews = {}",
        general.reviewer_reviews
    )?;
    writeln!(template)?;
    writeln!(template, "# Project-specific context levels")?;
    writeln!(
        template,
        "# developer_context = {}",
        general.developer_context
    )?;
    writeln!(
        template,
        "# reviewer_context = {}",
        general.reviewer_context
    )?;
    writeln!(template)?;
    write!(
        template,
        "{}",
        render_agent_chain_metadata_comments(&resolved_drains)
    )?;
    writeln!(template)?;
    writeln!(template, "# [agent_chains]")?;
    writeln!(template, "# Reusable named chain definitions")?;
    writeln!(template, "{rendered_chain_definitions}")?;
    writeln!(template)?;
    writeln!(template, "# [agent_drains]")?;
    writeln!(template, "# Built-in drains attached to those chains")?;
    writeln!(template, "{rendered_drain_bindings}")?;

    Ok(template)
}

fn render_agent_chain_metadata_comments(
    resolved_drains: &crate::agents::fallback::ResolvedDrainConfig,
) -> String {
    let mut rendered = String::new();
    let _ = writeln!(rendered, "# [agent_chain]");
    let _ = writeln!(
        rendered,
        "# Effective retry/backoff metadata you can override locally"
    );
    let _ = writeln!(rendered, "# max_retries = {}", resolved_drains.max_retries);
    let _ = writeln!(
        rendered,
        "# retry_delay_ms = {}",
        resolved_drains.retry_delay_ms
    );
    let _ = writeln!(
        rendered,
        "# backoff_multiplier = {}",
        resolved_drains.backoff_multiplier
    );
    let _ = writeln!(
        rendered,
        "# max_backoff_ms = {}",
        resolved_drains.max_backoff_ms
    );
    let _ = writeln!(rendered, "# max_cycles = {}", resolved_drains.max_cycles);

    let mut provider_fallback_entries =
        resolved_drains.provider_fallback.iter().collect::<Vec<_>>();
    provider_fallback_entries.sort_by(|(left, _), (right, _)| left.cmp(right));
    for (provider, models) in provider_fallback_entries {
        let _ = writeln!(
            rendered,
            "# provider_fallback.{provider} = {}",
            format_toml_string_array(models)
        );
    }

    rendered
}

fn resolve_template_drains(
    effective: &UnifiedConfig,
    default_drains: &crate::agents::fallback::ResolvedDrainConfig,
) -> anyhow::Result<crate::agents::fallback::ResolvedDrainConfig> {
    match effective.resolve_agent_drains_checked() {
        Ok(Some(resolved)) => Ok(resolved),
        Ok(None) => Ok(default_drains.clone()),
        Err(message)
            if named_chain_template_can_fall_back_to_defaults(effective, message.as_str()) =>
        {
            Ok(default_drains.clone())
        }
        Err(message) => Err(anyhow::Error::msg(message)),
    }
}

fn named_chain_template_can_fall_back_to_defaults(
    effective: &UnifiedConfig,
    message: &str,
) -> bool {
    !effective.agent_chains.is_empty()
        && effective.agent_drains.is_empty()
        && message.contains("agent_drains does not resolve all built-in drains")
}

fn resolve_effective_init_template_config<R: ConfigEnvironment>(
    env: &R,
) -> anyhow::Result<UnifiedConfig> {
    let Some(global_path) = env.unified_config_path() else {
        return Ok(UnifiedConfig::default());
    };

    if !env.file_exists(&global_path) {
        return Ok(UnifiedConfig::default());
    }

    let global_content = env.read_file(&global_path).map_err(|e| {
        anyhow::anyhow!(
            "Failed to read global config {} while generating local config template: {e}",
            global_path.display()
        )
    })?;

    let global = UnifiedConfig::load_from_content(&global_content).map_err(|e| {
        anyhow::anyhow!(
            "Failed to parse global config {} while generating local config template: {e}",
            global_path.display()
        )
    })?;

    Ok(UnifiedConfig::default().merge_with_content(&global_content, &global))
}

fn built_in_default_drains() -> anyhow::Result<crate::agents::fallback::ResolvedDrainConfig> {
    AgentRegistry::new()
        .map(|registry| registry.resolved_drains().clone())
        .map_err(map_registry_init_error)
}

fn map_registry_init_error(error: impl std::fmt::Display) -> anyhow::Error {
    anyhow::anyhow!("Failed to load built-in default agent chains: {error}")
}

/// Format a string slice as a TOML array literal (e.g. `["claude", "codex"]`).
fn format_toml_string_array(items: &[String]) -> String {
    let inner: Vec<String> = items.iter().map(|s| format!(r#""{s}""#)).collect();
    format!("[{}]", inner.join(", "))
}

fn collect_named_chain_definitions(
    effective: &UnifiedConfig,
    resolved: &crate::agents::fallback::ResolvedDrainConfig,
) -> BTreeMap<String, Vec<String>> {
    let mut chain_definitions: BTreeMap<String, Vec<String>> =
        effective.agent_chains.clone().into_iter().collect();

    for drain in crate::agents::AgentDrain::all() {
        let binding = resolved
            .binding(drain)
            .expect("built-in drain bindings should be fully resolved");
        chain_definitions
            .entry(binding.chain_name.clone())
            .or_insert_with(|| binding.agents.clone());
    }

    chain_definitions
}

/// Handle the `--init-local-config` flag with a custom path resolver.
///
/// Creates a local config file at `.agent/ralph-workflow.toml` in the current directory.
/// The generated template shows the user's current effective configuration values
/// (from global config or built-in defaults) as commented-out entries.
///
/// # Arguments
///
/// * `colors` - Terminal color configuration for output
/// * `env` - Path resolver for determining config file location
/// * `force` - Whether to overwrite existing config file
///
/// # Returns
///
/// Returns `Ok(true)` if the flag was handled (program should exit after),
/// or an error if config creation failed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_init_local_config_with<R: ConfigEnvironment>(
    colors: Colors,
    env: &R,
    force: bool,
) -> anyhow::Result<bool> {
    let local_path = env
        .local_config_path()
        .ok_or_else(|| anyhow::anyhow!("Cannot determine local config path"))?;

    // Check if config already exists
    if env.file_exists(&local_path) && !force {
        println!(
            "{}Local config already exists:{} {}",
            colors.yellow(),
            colors.reset(),
            local_path.display()
        );
        println!("Use --force-overwrite to replace it, or edit the existing file.");
        println!();
        println!("Run `ralph --check-config` to see effective configuration.");
        return Ok(true);
    }

    // Generate template populated with current effective values
    let template = generate_local_config_template(env)?;

    // Create config using the environment's file operations
    env.write_file(&local_path, &template).map_err(|e| {
        anyhow::anyhow!(
            "Failed to create local config file {}: {}",
            local_path.display(),
            e
        )
    })?;

    // Try to show absolute path, fall back to the path as-is if canonicalization fails
    let display_path = local_path
        .canonicalize()
        .unwrap_or_else(|_| local_path.clone());

    println!(
        "{}Created{} {}",
        colors.green(),
        colors.reset(),
        display_path.display()
    );
    println!();
    println!(
        "This local config will override your global settings (~/.config/ralph-workflow.toml)."
    );
    println!("Edit the file to customize Ralph for this project.");
    println!();
    println!("Tip: Run `ralph --check-config` to validate your configuration.");

    Ok(true)
}

/// Handle the `--init-local-config` flag using the default path resolver.
///
/// Convenience wrapper that uses [`RealConfigEnvironment`] internally.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_init_local_config(colors: Colors, force: bool) -> anyhow::Result<bool> {
    handle_init_local_config_with(colors, &RealConfigEnvironment, force)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::path_resolver::MemoryConfigEnvironment;
    use std::path::Path;

    #[test]
    fn test_init_local_config_shows_global_values() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml")
            .with_file(
                "/test/config/ralph-workflow.toml",
                "\n[general]\ndeveloper_iters = 8\nreviewer_reviews = 3\n\
                 developer_context = 2\nreviewer_context = 1\n\
                 \n[agent_chain]\nmax_retries = 9\nretry_delay_ms = 2500\n\
                 backoff_multiplier = 3.0\nmax_backoff_ms = 90000\n\
                 provider_fallback.opencode = [\"-m opencode/glm-4.7-free\"]\n",
            );

        handle_init_local_config_with(Colors::new(), &env, false).unwrap();

        let content = env
            .get_file(Path::new("/test/repo/.agent/ralph-workflow.toml"))
            .expect("local config should be written");

        // Should reflect global values, not built-in defaults
        assert!(
            content.contains("developer_iters = 8"),
            "should show global developer_iters=8, got:\n{content}"
        );
        assert!(
            content.contains("reviewer_reviews = 3"),
            "should show global reviewer_reviews=3, got:\n{content}"
        );
        assert!(
            content.contains("developer_context = 2"),
            "should show global developer_context=2, got:\n{content}"
        );
        assert!(
            content.contains("reviewer_context = 1"),
            "should show global reviewer_context=1, got:\n{content}"
        );
        assert!(
            content.contains("# [agent_chain]"),
            "should include the effective agent_chain metadata section, got:\n{content}"
        );
        assert!(
            content.contains("# max_retries = 9"),
            "should show global max_retries=9, got:\n{content}"
        );
        assert!(
            content.contains("# retry_delay_ms = 2500"),
            "should show global retry_delay_ms=2500, got:\n{content}"
        );
        assert!(
            content.contains("# backoff_multiplier = 3"),
            "should show global backoff_multiplier=3.0, got:\n{content}"
        );
        assert!(
            content.contains("# max_backoff_ms = 90000"),
            "should show global max_backoff_ms=90000, got:\n{content}"
        );
        assert!(
            content.contains(r#"# provider_fallback.opencode = ["-m opencode/glm-4.7-free"]"#),
            "should show global provider fallback metadata, got:\n{content}"
        );
    }

    #[test]
    fn test_init_local_config_uses_defaults_without_global() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml");
        // No global config file exists

        handle_init_local_config_with(Colors::new(), &env, false).unwrap();

        let content = env
            .get_file(Path::new("/test/repo/.agent/ralph-workflow.toml"))
            .expect("local config should be written");

        // Should fall back to built-in defaults
        assert!(
            content.contains("developer_iters = 5"),
            "should show default developer_iters=5, got:\n{content}"
        );
        assert!(
            content.contains("reviewer_reviews = 2"),
            "should show default reviewer_reviews=2, got:\n{content}"
        );
    }

    #[test]
    fn test_init_local_config_fails_when_built_in_default_chain_cannot_be_loaded() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml");

        let err = generate_local_config_template_with(&env, || {
            Err(anyhow::anyhow!("simulated built-in agents load failure"))
        })
        .expect_err("template generation should fail when built-in defaults cannot be loaded");
        let msg = err.to_string();

        assert!(
            msg.contains("simulated built-in agents load failure"),
            "error should include built-in chain load failure reason, got:\n{msg}"
        );
        assert!(
            !env.was_written(Path::new("/test/repo/.agent/ralph-workflow.toml")),
            "local config should not be created when template generation fails"
        );
    }

    #[test]
    fn test_init_local_config_uses_built_in_agent_chain_defaults_without_global() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml");

        handle_init_local_config_with(Colors::new(), &env, false).unwrap();

        let content = env
            .get_file(Path::new("/test/repo/.agent/ralph-workflow.toml"))
            .expect("local config should be written");

        let registry = crate::agents::AgentRegistry::new().expect("built-in registry should load");
        let builtins = registry.resolved_drains();
        let expected_developer = format_toml_string_array(
            &builtins
                .binding(crate::agents::AgentDrain::Development)
                .expect("development drain")
                .agents,
        );
        let expected_reviewer = format_toml_string_array(
            &builtins
                .binding(crate::agents::AgentDrain::Review)
                .expect("review drain")
                .agents,
        );

        assert!(
            content.contains(&format!("developer = {expected_developer}")),
            "should use built-in developer chain defaults, got:\n{content}"
        );
        assert!(
            content.contains(&format!("reviewer = {expected_reviewer}")),
            "should use built-in reviewer chain defaults, got:\n{content}"
        );
        assert!(
            content.contains(r#"# planning = "developer""#),
            "should include the planning drain binding, got:\n{content}"
        );
        assert!(
            content.contains(r#"# review = "reviewer""#),
            "should include the review drain binding, got:\n{content}"
        );
    }

    #[test]
    fn test_init_local_config_shows_global_agent_chains() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml")
            .with_file(
                "/test/config/ralph-workflow.toml",
                r#"
[agent_chain]
developer = ["codex", "claude"]
reviewer = ["claude"]
"#,
            );

        handle_init_local_config_with(Colors::new(), &env, false).unwrap();

        let content = env
            .get_file(Path::new("/test/repo/.agent/ralph-workflow.toml"))
            .expect("local config should be written");

        assert!(
            content.contains(r#"developer = ["codex", "claude"]"#),
            "should show global developer chain, got:\n{content}"
        );
        assert!(
            content.contains(r#"reviewer = ["claude"]"#),
            "should show global reviewer chain, got:\n{content}"
        );
        assert!(
            content.contains(r#"# development = "developer""#),
            "should show the development drain binding, got:\n{content}"
        );
        assert!(
            content.contains(r#"# review = "reviewer""#),
            "should show the review drain binding, got:\n{content}"
        );

        let named_env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml")
            .with_file(
                "/test/config/ralph-workflow.toml",
                r#"
[agent_chains]
shared_dev = ["codex", "claude"]
shared_review = ["claude"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
review = "shared_review"
fix = "shared_review"
commit = "shared_review"
analysis = "shared_dev"
"#,
            );

        handle_init_local_config_with(Colors::new(), &named_env, true).unwrap();

        let content = named_env
            .get_file(Path::new("/test/repo/.agent/ralph-workflow.toml"))
            .expect("local config should be written");

        assert!(
            content.contains(r#"shared_dev = ["codex", "claude"]"#),
            "should show the named shared_dev chain, got:\n{content}"
        );
        assert!(
            content.contains(r#"shared_review = ["claude"]"#),
            "should show the named shared_review chain, got:\n{content}"
        );
        assert!(
            content.contains(r#"# planning = "shared_dev""#),
            "should preserve planning drain binding, got:\n{content}"
        );
        assert!(
            content.contains(r#"# commit = "shared_review""#),
            "should preserve commit drain binding, got:\n{content}"
        );
    }

    #[test]
    fn test_init_local_config_partial_global_agent_chain_uses_builtin_missing_roles() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml")
            .with_file(
                "/test/config/ralph-workflow.toml",
                r#"
[agent_chain]
developer = ["codex"]
"#,
            );

        handle_init_local_config_with(Colors::new(), &env, false).unwrap();

        let content = env
            .get_file(Path::new("/test/repo/.agent/ralph-workflow.toml"))
            .expect("local config should be written");

        let registry = crate::agents::AgentRegistry::new().expect("built-in registry should load");
        let builtins = registry.resolved_drains();
        let expected_reviewer = format_toml_string_array(
            &builtins
                .binding(crate::agents::AgentDrain::Review)
                .expect("review drain")
                .agents,
        );

        assert!(
            content.contains(r#"developer = ["codex"]"#),
            "should show global developer chain, got:\n{content}"
        );
        assert!(
            content.contains(&format!("reviewer = {expected_reviewer}")),
            "missing global reviewer should fall back to built-in defaults, got:\n{content}"
        );
        assert!(
            content.contains(r#"# development = "developer""#),
            "should bind development to the developer chain, got:\n{content}"
        );
        assert!(
            content.contains(r#"# review = "reviewer""#),
            "should bind review to the reviewer chain, got:\n{content}"
        );
    }

    #[test]
    fn test_init_local_config_generates_valid_toml_structure() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml")
            .with_file(
                "/test/config/ralph-workflow.toml",
                "[general]\ndeveloper_iters = 8",
            );

        handle_init_local_config_with(Colors::new(), &env, false).unwrap();

        let content = env
            .get_file(Path::new("/test/repo/.agent/ralph-workflow.toml"))
            .expect("local config should be written");

        // All value lines should be comments (starts with #)
        // so the file is valid TOML as-is (all commented out)
        assert!(
            content.contains("[general]"),
            "should have [general] section, got:\n{content}"
        );
        assert!(
            content.contains("# developer_iters"),
            "values should be commented out, got:\n{content}"
        );
    }

    #[test]
    fn test_format_toml_string_array() {
        assert_eq!(
            format_toml_string_array(&["claude".to_string()]),
            r#"["claude"]"#
        );
        assert_eq!(
            format_toml_string_array(&["codex".to_string(), "claude".to_string()]),
            r#"["codex", "claude"]"#
        );
        assert_eq!(format_toml_string_array(&[]), r"[]");
    }

    #[test]
    fn test_init_local_config_at_worktree_root() {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_worktree_root("/test/main-repo")
            .with_file(
                "/test/config/ralph-workflow.toml",
                "[general]\nverbosity = 2",
            );

        handle_init_local_config_with(Colors::new(), &env, false).unwrap();

        assert!(
            env.was_written(Path::new("/test/main-repo/.agent/ralph-workflow.toml")),
            "Config should be written at canonical repo root"
        );
    }
}
