//! I/O boundary for cloud network operations.
//!
//! This module contains the imperative network I/O code that cannot be
//! expressed functionally. The HTTP client implementation uses ureq
//! for making HTTP requests to the cloud API.

pub mod redaction;

use crate::cloud::types::{interpret_http_response, CloudError, PipelineResult, ProgressUpdate};
use crate::cloud::CloudReporter;
use crate::config::types::CloudConfig;

pub struct HttpCloudReporter {
    config: CloudConfig,
}

impl HttpCloudReporter {
    #[must_use]
    pub const fn new(config: CloudConfig) -> Self {
        Self { config }
    }

    pub(crate) fn build_url(api_url: &str, path: &str) -> Result<String, CloudError> {
        let base = api_url.trim();
        if !base.to_ascii_lowercase().starts_with("https://") {
            return Err(CloudError::Configuration(
                "Cloud API URL must use https://".to_string(),
            ));
        }

        let base = base.trim_end_matches('/');
        let path = path.trim_start_matches('/');

        if path.is_empty() {
            return Ok(base.to_string());
        }

        Ok(format!("{base}/{path}"))
    }

    pub(crate) fn post_json<T: serde::Serialize>(
        &self,
        path: &str,
        body: &T,
    ) -> Result<(), CloudError> {
        let (url, api_token) = self.extract_credentials(path)?;
        let json_body =
            serde_json::to_value(body).map_err(|e| CloudError::Serialization(e.to_string()))?;
        let (status, body) = perform_request(&url, &api_token, json_body)?;
        interpret_http_response(status, body)
    }

    fn extract_credentials(&self, path: &str) -> Result<(String, String), CloudError> {
        let api_url = self
            .config
            .api_url
            .as_ref()
            .ok_or_else(|| CloudError::Configuration("API URL not configured".to_string()))?;
        let api_token = self
            .config
            .api_token
            .as_ref()
            .ok_or_else(|| CloudError::Configuration("API token not configured".to_string()))?;
        let url = Self::build_url(api_url, path)?;
        Ok((url, api_token.clone()))
    }
}

fn perform_request(
    url: &str,
    api_token: &str,
    json_body: serde_json::Value,
) -> Result<(u16, String), CloudError> {
    let agent = ureq::Agent::new_with_config(
        ureq::config::Config::builder()
            .timeout_global(Some(std::time::Duration::from_secs(30)))
            .http_status_as_error(false)
            .build(),
    );
    let response = agent
        .post(url)
        .header("Authorization", &format!("Bearer {api_token}"))
        .header("Content-Type", "application/json")
        .send_json(json_body);

    match response {
        Ok(resp) => {
            let status = resp.status().as_u16();
            let body = resp.into_body().read_to_string()?;
            Ok((status, body))
        }
        Err(e) => Err(CloudError::NetworkError(e.to_string())),
    }
}

impl CloudReporter for HttpCloudReporter {
    fn report_progress(&self, update: &ProgressUpdate) -> Result<(), CloudError> {
        let run_id = self
            .config
            .run_id
            .as_ref()
            .ok_or_else(|| CloudError::Configuration("Run ID not configured".to_string()))?;

        let path = format!("runs/{run_id}/progress");
        self.post_json(&path, update)
    }

    fn heartbeat(&self) -> Result<(), CloudError> {
        let run_id = self
            .config
            .run_id
            .as_ref()
            .ok_or_else(|| CloudError::Configuration("Run ID not configured".to_string()))?;

        let path = format!("runs/{run_id}/heartbeat");
        let body = serde_json::json!({
            "timestamp": chrono::Utc::now().to_rfc3339(),
        });
        self.post_json(&path, &body)
    }

    fn report_completion(&self, result: &PipelineResult) -> Result<(), CloudError> {
        let run_id = self
            .config
            .run_id
            .as_ref()
            .ok_or_else(|| CloudError::Configuration("Run ID not configured".to_string()))?;

        let path = format!("runs/{run_id}/complete");
        self.post_json(&path, result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::types::CloudConfig;

    #[test]
    fn test_build_url_trims_slashes_and_joins_paths() {
        let base = "https://api.example.com/v1/";
        let url = HttpCloudReporter::build_url(base, "/runs/run_1/progress").unwrap();
        assert_eq!(
            url, "https://api.example.com/v1/runs/run_1/progress",
            "URL join should avoid double slashes"
        );
    }

    #[test]
    fn test_build_url_rejects_non_https() {
        let err = HttpCloudReporter::build_url("http://api.example.com", "/runs/x").unwrap_err();
        match err {
            CloudError::Configuration(_) => {}
            other => panic!("expected Configuration error, got: {other:?}"),
        }
    }

    #[test]
    fn test_http_reporter_requires_config() {
        let config = CloudConfig::disabled();
        let reporter = HttpCloudReporter::new(config);

        let update = ProgressUpdate {
            timestamp: "2025-02-15T10:00:00Z".to_string(),
            phase: "Planning".to_string(),
            previous_phase: None,
            iteration: Some(1),
            total_iterations: Some(3),
            review_pass: None,
            total_review_passes: None,
            message: "Test".to_string(),
            event_type: crate::cloud::types::ProgressEventType::PipelineStarted,
        };

        assert!(reporter.report_progress(&update).is_err());
    }
}
