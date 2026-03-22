//! Boundary wiring: connects the `io` HTTP capability to the `opencode_api` domain traits.
//!
//! This module lives at a `boundary`-named path so it is exempt from the
//! `forbid_domain_boundary_dependencies` lint, allowing it to import directly
//! from `crate::io`. All other `opencode_api` modules interact with HTTP only
//! through the `fetch::HttpFetcher` trait defined in the domain.

use crate::io::http_fetch::RealHttpFetcher;

use super::fetch;
use super::RealCatalogLoader;

impl fetch::HttpFetcher for RealHttpFetcher {
    fn fetch(&self, url: &str) -> Result<String, fetch::HttpFetchError> {
        crate::io::http_fetch::HttpFetcher::fetch(self, url)
            .map_err(fetch::HttpFetchError::RequestFailed)
    }
}

impl Default for RealCatalogLoader {
    fn default() -> Self {
        let fetcher = fetch::RealCatalogFetcher::with_http_fetcher(RealHttpFetcher);
        RealCatalogLoader::with_fetcher(fetcher)
    }
}
