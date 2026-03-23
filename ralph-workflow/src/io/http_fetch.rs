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
    use mockito::Server;

    #[test]
    fn fetch_url_returns_mocked_body() {
        let mut server = Server::new();
        let _mock = server
            .mock("GET", "/test")
            .with_status(200)
            .with_body("test content")
            .create();

        let url = format!("{}/test", server.url());
        let result = fetch_url(&url).unwrap();

        assert_eq!(result, "test content");
    }

    #[test]
    fn fetch_url_propagates_error_status() {
        let mut server = Server::new();
        let _mock = server
            .mock("GET", "/error")
            .with_status(500)
            .with_body("server error")
            .create();

        let url = format!("{}/error", server.url());
        let error = fetch_url(&url).unwrap_err();

        assert!(error.contains("500"));
        assert!(error.contains("server error"));
    }
}
