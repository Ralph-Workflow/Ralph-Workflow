use serde::{Deserialize, Serialize};
use specta::Type;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};
use tokio::fs;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

const PREFERENCES_FILE: &str = "gui_preferences.json";

/// Which run events should trigger a desktop notification.
///
/// Split from [`GuiNotificationSettings`] so that no single struct exceeds
/// three boolean fields (pedantic `struct_excessive_bools` lint).
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
#[serde(rename_all = "camelCase", default)]
pub struct GuiNotificationTriggers {
    pub notify_completion: bool,
    pub notify_failure: bool,
    pub notify_degraded: bool,
}

impl Default for GuiNotificationTriggers {
    fn default() -> Self {
        Self {
            notify_completion: true,
            notify_failure: true,
            notify_degraded: true,
        }
    }
}

/// Notification-related preferences.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
#[serde(rename_all = "camelCase", default)]
pub struct GuiNotificationSettings {
    pub show_phase_notifications: bool,
    pub desktop_notifications: bool,
    pub notify_phase_change: bool,
    pub triggers: GuiNotificationTriggers,
}

impl Default for GuiNotificationSettings {
    fn default() -> Self {
        Self {
            show_phase_notifications: true,
            desktop_notifications: true,
            notify_phase_change: false,
            triggers: GuiNotificationTriggers::default(),
        }
    }
}

/// Session and log behaviour preferences.
///
/// Split from the top-level [`GuiPreferences`] struct so that no single struct
/// exceeds three boolean fields (pedantic `struct_excessive_bools` lint).
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
#[serde(rename_all = "camelCase", default)]
pub struct GuiSessionSettings {
    pub log_autoscroll: bool,
    pub confirm_cancel: bool,
    pub restore_workspaces: bool,
}

impl Default for GuiSessionSettings {
    fn default() -> Self {
        Self {
            log_autoscroll: true,
            confirm_cancel: true,
            restore_workspaces: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Type)]
#[serde(rename_all = "camelCase", default)]
pub struct GuiPreferences {
    pub theme: String,
    pub accent_color: String,
    pub sidebar_width: u32,
    pub font_size: u32,
    pub monospace_font: String,
    pub run_poll_interval_ms: u32,
    pub log_buffer_size: u32,
    pub default_view: String,
    pub check_updates: bool,
    pub session: GuiSessionSettings,
    pub notifications: GuiNotificationSettings,
}

impl Default for GuiPreferences {
    fn default() -> Self {
        Self {
            theme: "dark".to_string(),
            accent_color: "#f59e0b".to_string(),
            sidebar_width: 220,
            font_size: 14,
            monospace_font: "JetBrains Mono".to_string(),
            run_poll_interval_ms: 2000,
            log_buffer_size: 10000,
            default_view: "/".to_string(),
            check_updates: true,
            session: GuiSessionSettings::default(),
            notifications: GuiNotificationSettings::default(),
        }
    }
}

fn get_preferences_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map(|p| p.join(PREFERENCES_FILE))
        .map_err(|e| format!("Failed to get app data dir: {e}"))
}

/// Load GUI preferences from disk.
///
/// # Errors
///
/// Returns an error if the preferences file cannot be read or parsed.
#[tauri::command]
#[specta::specta]
pub async fn get_gui_preferences(app: AppHandle) -> Result<GuiPreferences, String> {
    let path = get_preferences_path(&app)?;

    let path_exists = tokio::fs::try_exists(&path)
        .await
        .map_err(|e| format!("Failed to check preferences file existence: {e}"))?;
    if !path_exists {
        return Ok(GuiPreferences::default());
    }

    let mut file = fs::File::open(&path)
        .await
        .map_err(|e| format!("Failed to open preferences file: {e}"))?;

    let mut contents = String::new();
    file.read_to_string(&mut contents)
        .await
        .map_err(|e| format!("Failed to read preferences file: {e}"))?;

    if contents.trim().is_empty() {
        return Ok(GuiPreferences::default());
    }

    serde_json::from_str(&contents).map_err(|e| format!("Failed to parse preferences JSON: {e}"))
}

/// Persist GUI preferences to disk.
///
/// # Errors
///
/// Returns an error if the preferences file cannot be written.
#[tauri::command]
#[specta::specta]
pub async fn save_gui_preferences(app: AppHandle, prefs: GuiPreferences) -> Result<(), String> {
    let path = get_preferences_path(&app)?;

    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .await
            .map_err(|e| format!("Failed to create preferences directory: {e}"))?;
    }

    let contents = serde_json::to_string_pretty(&prefs)
        .map_err(|e| format!("Failed to serialize preferences: {e}"))?;

    let mut file = fs::File::create(&path)
        .await
        .map_err(|e| format!("Failed to create preferences file: {e}"))?;

    file.write_all(contents.as_bytes())
        .await
        .map_err(|e| format!("Failed to write preferences file: {e}"))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o600);
        tokio::fs::set_permissions(&path, perms)
            .await
            .map_err(|e| format!("Failed to set file permissions: {e}"))?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;
    use tokio::fs;

    #[tokio::test]
    async fn test_default_preferences() {
        let prefs = GuiPreferences::default();
        assert_eq!(prefs.theme, "dark");
        assert_eq!(prefs.accent_color, "#f59e0b");
        assert_eq!(prefs.sidebar_width, 220);
        assert_eq!(prefs.font_size, 14);
        assert!(prefs.session.log_autoscroll);
        assert!(prefs.session.confirm_cancel);
        assert!(prefs.session.restore_workspaces);
    }

    #[tokio::test]
    async fn test_serialize_deserialize() {
        let prefs = GuiPreferences::default();
        let json = serde_json::to_string(&prefs).unwrap();
        let parsed: GuiPreferences = serde_json::from_str(&json).unwrap();
        assert_eq!(prefs.theme, parsed.theme);
        assert_eq!(prefs.sidebar_width, parsed.sidebar_width);
    }

    #[tokio::test]
    async fn test_save_and_load_preferences() {
        let dir = tempdir().expect("Failed to create temp dir");
        let path = dir.path().join(PREFERENCES_FILE);

        let prefs = GuiPreferences {
            theme: "dark".to_string(),
            accent_color: "#custom".to_string(),
            sidebar_width: 300,
            font_size: 16,
            monospace_font: "Fira Code".to_string(),
            run_poll_interval_ms: 3000,
            log_buffer_size: 5000,
            default_view: "/sessions".to_string(),
            check_updates: false,
            notifications: GuiNotificationSettings {
                show_phase_notifications: false,
                desktop_notifications: false,
                notify_phase_change: true,
                triggers: GuiNotificationTriggers {
                    notify_completion: false,
                    notify_failure: true,
                    notify_degraded: false,
                },
            },
            session: GuiSessionSettings {
                log_autoscroll: false,
                confirm_cancel: false,
                restore_workspaces: false,
            },
        };

        let contents = serde_json::to_string_pretty(&prefs).unwrap();
        fs::write(&path, contents)
            .await
            .expect("Failed to write test file");

        let loaded_contents = fs::read_to_string(&path)
            .await
            .expect("Failed to read test file");
        let loaded: GuiPreferences =
            serde_json::from_str(&loaded_contents).expect("Failed to parse");

        assert_eq!(loaded.theme, prefs.theme);
        assert_eq!(loaded.sidebar_width, prefs.sidebar_width);
        assert_eq!(loaded.session.log_autoscroll, prefs.session.log_autoscroll);
    }

    #[tokio::test]
    async fn test_parse_empty_json_returns_defaults() {
        // With #[serde(default)], missing fields fall back to Default::default().
        let loaded: GuiPreferences =
            serde_json::from_str("{}").expect("Failed to parse empty JSON");
        assert_eq!(loaded.theme, GuiPreferences::default().theme);
        assert_eq!(loaded.font_size, GuiPreferences::default().font_size);
    }
}
