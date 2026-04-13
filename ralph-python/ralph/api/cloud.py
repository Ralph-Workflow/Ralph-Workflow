"""Cloud reporting integration for Ralph pipeline.

This module provides optional cloud reporting via HTTP API,
allowing Ralph to report pipeline status to an external
cloud service when enabled in configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from loguru import logger
from pydantic import BaseModel

if TYPE_CHECKING:
    from ralph.config.models import CloudConfig


class PipelineReport(BaseModel):
    """Pipeline execution report for cloud reporting.

    Attributes:
        phase: Current pipeline phase.
        iteration: Current iteration number.
        total_iterations: Total number of iterations.
        success: Whether pipeline completed successfully.
        error: Error message if failed.
    """

    phase: str
    iteration: int
    total_iterations: int
    success: bool
    error: str | None = None


class CloudReporter:
    """Cloud reporting client for pipeline status.

    This reporter sends pipeline execution reports to an external
    cloud API when cloud reporting is enabled in configuration.

    Attributes:
        config: Cloud configuration.
    """

    def __init__(self, config: CloudConfig) -> None:
        """Initialize cloud reporter.

        Args:
            config: Cloud configuration with API endpoint and credentials.
        """
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client.

        Returns:
            Configured httpx Client instance.
        """
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.config.timeout_secs,
                headers=self._get_headers(),
            )
        return self._client

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for API requests.

        Returns:
            Dictionary of HTTP headers.
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def report(self, report: PipelineReport) -> bool:
        """Send pipeline report to cloud API.

        Args:
            report: Pipeline execution report.

        Returns:
            True if report was sent successfully.

        Raises:
            httpx.HTTPError: If the request fails and is not recoverable.
        """
        if not self.config.enabled or not self.config.api_url:
            logger.debug("Cloud reporting disabled, skipping")
            return False

        try:
            client = self._get_client()
            response = client.post(
                str(self.config.api_url),
                json=report.model_dump(),
            )
            response.raise_for_status()
            logger.debug("Cloud report sent successfully")
            return True
        except httpx.HTTPError as exc:
            logger.warning("Failed to send cloud report: {}", exc)
            return False

    def report_start(self, total_iterations: int) -> bool:
        """Report pipeline start.

        Args:
            total_iterations: Total number of planned iterations.

        Returns:
            True if report was sent successfully.
        """
        return self.report(
            PipelineReport(
                phase="start",
                iteration=0,
                total_iterations=total_iterations,
                success=True,
            )
        )

    def report_progress(
        self,
        phase: str,
        iteration: int,
        total_iterations: int,
    ) -> bool:
        """Report pipeline progress.

        Args:
            phase: Current phase.
            iteration: Current iteration.
            total_iterations: Total iterations.

        Returns:
            True if report was sent successfully.
        """
        return self.report(
            PipelineReport(
                phase=phase,
                iteration=iteration,
                total_iterations=total_iterations,
                success=True,
            )
        )

    def report_complete(
        self,
        phase: str,
        iteration: int,
        total_iterations: int,
    ) -> bool:
        """Report pipeline completion.

        Args:
            phase: Final phase.
            iteration: Final iteration.
            total_iterations: Total iterations.

        Returns:
            True if report was sent successfully.
        """
        return self.report(
            PipelineReport(
                phase=phase,
                iteration=iteration,
                total_iterations=total_iterations,
                success=True,
            )
        )

    def report_failure(
        self,
        phase: str,
        iteration: int,
        total_iterations: int,
        error: str,
    ) -> bool:
        """Report pipeline failure.

        Args:
            phase: Phase where failure occurred.
            iteration: Iteration where failure occurred.
            total_iterations: Total iterations.
            error: Error message.

        Returns:
            True if report was sent successfully.
        """
        return self.report(
            PipelineReport(
                phase=phase,
                iteration=iteration,
                total_iterations=total_iterations,
                success=False,
                error=error,
            )
        )

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> CloudReporter:
        """Context manager entry."""
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit."""
        self.close()
