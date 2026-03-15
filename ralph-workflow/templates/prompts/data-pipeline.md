# Data Pipeline: [Name or purpose]

## Purpose
<!-- Why does this data need to move? What decisions will it inform? -->
[e.g., "Enable product team to see daily sales trends for inventory planning"]

## Data Flow
<!-- Where does data come from and go to? -->
- **Source:** [e.g., "PostgreSQL orders table"]
- **Destination:** [e.g., "Analytics data warehouse"]

## Transformation
<!-- What happens to the data? What's the output shape? -->
[e.g., "Aggregate orders by product category and day, calculate totals and averages"]

## Schedule
<!-- How often should this run? -->
[e.g., "Daily at 2am UTC" or "Real-time streaming" or "On-demand triggered by API"]

## Failure Handling
<!-- What happens when something goes wrong? -->
- [e.g., "Source unavailable → retry 3x, then alert"]
- [e.g., "Partial failure → checkpoint and resume"]
- [e.g., "Bad data → quarantine and continue"]

## Volume (optional)
<!-- Expected data volume -->
[e.g., "~1M records per day, growing 10% monthly"]

## Context (optional)
<!-- Existing pipeline patterns, orchestration tools -->
[e.g., "Use existing Airflow setup in /pipelines/"]
