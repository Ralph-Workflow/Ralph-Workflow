# API Integration: [Name of the external API]

## Purpose
<!-- What capability does this integration add? What user problem does it solve? -->
[e.g., "Enable transactional email so users receive order confirmations"]

## User Impact
<!-- How does this affect the end user experience? -->
[e.g., "Users receive email confirmations within seconds of placing an order"]

## Endpoints Needed
<!-- Which external API endpoints will you use? -->
- [e.g., "POST /mail/send - Send emails"]
- [e.g., "GET /stats - Track delivery status"]

## Data Flow
<!-- How does data flow through the integration? -->
[e.g., "Order placed → our service formats email → SendGrid sends → webhook confirms delivery → we update order status"]

## Failure Scenarios
<!-- What happens when the external API fails? -->
- [e.g., "API timeout → queue for retry, user sees 'confirmation pending'"]
- [e.g., "Invalid API key → alert ops, fail gracefully with user message"]
- [e.g., "Rate limited → exponential backoff, batch if possible"]

## Credentials (optional)
<!-- Where are API keys stored? -->
[e.g., "API key in SENDGRID_API_KEY environment variable"]

## Context (optional)
<!-- Existing integration patterns, fallback services -->
[e.g., "Should follow existing external service patterns in /services/external/"]
