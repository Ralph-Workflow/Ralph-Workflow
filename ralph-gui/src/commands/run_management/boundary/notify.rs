use tauri_plugin_notification::NotificationExt;

/// Determine notification title/body for given run status.
#[must_use]
pub fn notification_params_for_status(
    status: &str,
    run_id: &str,
    context: &str,
) -> (String, Option<String>) {
    match status {
        "Failed" => (
            "Ralph Run Failed".to_string(),
            Some(format!(
                "Run {run_id} failed in {context}. Resume or check logs."
            )),
        ),
        "Paused" => (
            "Ralph Run Paused".to_string(),
            Some(format!(
                "Run {run_id} in {context} was paused or interrupted."
            )),
        ),
        "Completed" => (
            "Ralph Run Completed".to_string(),
            Some(format!("Run {run_id} completed successfully in {context}.")),
        ),
        other => (format!("Ralph: {other}"), None),
    }
}

/// Send a desktop notification for a run status change.
///
/// Notification tiers:
/// - **Passive** (title only): Running and other routine transitions.
/// - **Interruptive** (title + body): Paused, Failed, and Completed transitions.
///
/// # Errors
///
/// Returns an error if the notification plugin is unavailable or the OS rejects the request.
/// The frontend should handle this gracefully and not surface notification errors to users.
#[tauri::command]
#[specta::specta]
pub fn notify_run_status_change(
    app: tauri::AppHandle,
    status: String,
    run_id: String,
    context: String,
) -> Result<(), String> {
    let (title, body) = notification_params_for_status(&status, &run_id, &context);

    let mut builder = app.notification().builder().title(&title);
    if let Some(ref body_text) = body {
        builder = builder.body(body_text.as_str());
    }

    builder
        .show()
        .map_err(|e| format!("Failed to send notification: {e}"))
}
