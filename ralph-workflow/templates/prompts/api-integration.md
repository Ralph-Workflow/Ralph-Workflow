# API Integration: [Name of the external API]

## Goal
<!-- What capability does this integration add to the system? -->
[e.g., "System can send transactional emails via SendGrid for order confirmations and notifications"]

## Integration Points
<!-- Which external API operations will we use? -->
- [e.g., "POST /mail/send - Send transactional emails"]
- [e.g., "Webhooks: delivery status callbacks"]

## Data Flow
<!-- How does data flow between systems? -->
```
[Internal Event] → [Our Service] → [External API] → [Callback/Webhook] → [State Update]

Example:
OrderPlaced → EmailService → SendGrid API → DeliveryWebhook → MarkEmailDelivered
```

## Failure Modes & Recovery
<!-- How should the system behave when the external API fails? -->
| Failure | Detection | Recovery |
|---------|-----------|----------|
| [e.g., "API timeout"] | [e.g., ">5s response"] | [e.g., "Retry with backoff, max 3 attempts"] |
| [e.g., "Rate limited"] | [e.g., "429 response"] | [e.g., "Queue and retry after Retry-After header"] |
| [e.g., "API down"] | [e.g., "5xx errors"] | [e.g., "Queue to dead letter, alert ops"] |

## Configuration
<!-- What configuration is needed? -->
- [e.g., "API key: SENDGRID_API_KEY environment variable"]
- [e.g., "Webhook endpoint: /webhooks/sendgrid"]

## Non-Functional Requirements (optional)
<!-- SLAs, throughput, compliance -->
[e.g., "Must handle 10k emails/hour" or "Must comply with GDPR (no PII in logs)"]

## Acceptance
<!-- What must be true for this integration to be complete? -->
- [ ] [e.g., "Happy path works end-to-end"]
- [ ] [e.g., "All failure modes handled per spec"]
- [ ] [e.g., "Configuration documented"]
- [ ] [e.g., "Monitoring/alerting in place"]
