use std::time::Duration;

fn build_agent() -> ureq::Agent {
    ureq::Agent::new_with_config(
        ureq::config::Config::builder()
            .timeout_global(Some(Duration::from_secs(10)))
            .http_status_as_error(false)
            .build(),
    )
}

fn check_http_status(status: ureq::http::StatusCode, body: &str) -> Result<(), String> {
    if status.is_client_error() || status.is_server_error() {
        if body.is_empty() {
            return Err(format!("status {}", status.as_u16()));
        }
        return Err(format!("status {}: {}", status.as_u16(), body));
    }
    Ok(())
}

pub fn fetch_url(url: &str) -> Result<String, String> {
    let agent = build_agent();
    let mut response = agent
        .get(url)
        .call()
        .map_err(|e: ureq::Error| e.to_string())?;

    let status = response.status();
    let body = response
        .body_mut()
        .read_to_string()
        .map_err(|e| e.to_string())?;

    check_http_status(status, &body)?;
    Ok(body)
}

pub trait HttpFetcher: Send + Sync {
    fn fetch(&self, url: &str) -> Result<String, String>;
}

#[derive(Debug, Clone, Default)]
pub struct RealHttpFetcher;

impl HttpFetcher for RealHttpFetcher {
    fn fetch(&self, url: &str) -> Result<String, String> {
        fetch_url(url)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ureq::http::StatusCode;

    #[test]
    fn check_http_status_allows_success_statuses() {
        let result = check_http_status(StatusCode::OK, "test content");
        assert!(result.is_ok());
    }

    #[test]
    fn check_http_status_propagates_error_status() {
        let error =
            check_http_status(StatusCode::INTERNAL_SERVER_ERROR, "server error").unwrap_err();

        assert!(error.contains("500"));
        assert!(error.contains("server error"));
    }

    #[test]
    fn check_http_status_uses_status_only_when_body_is_empty() {
        let error = check_http_status(StatusCode::BAD_REQUEST, "").unwrap_err();

        assert_eq!(error, "status 400");
    }
}
