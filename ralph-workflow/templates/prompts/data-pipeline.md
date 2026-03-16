# Data Pipeline: [Name or purpose]

## Goal
<!-- What does this pipeline accomplish? Who consumes the output? -->
[e.g., "Analytics team has daily aggregated sales data for inventory planning"]

## Data Flow
```
[Source] → [Transform] → [Destination]

[e.g., "PostgreSQL orders → Aggregate by product/day → Analytics warehouse"]
```

## Source
<!-- Where does the data come from? -->
- [e.g., "Table: orders (production PostgreSQL)"]
- [e.g., "Filter: orders.created_at >= yesterday"]
- [e.g., "Volume: ~100k records/day"]

## Transformation
<!-- What processing happens? -->
[e.g., "Group by product_category and date. Calculate: total_orders, total_revenue, avg_order_value"]

## Destination
<!-- Where does the data go? What's the output schema? -->
```
daily_sales {
  date: date
  category: string  
  total_orders: int
  total_revenue: decimal
  avg_order_value: decimal
}
```

## Schedule
<!-- When and how often does this run? -->
[e.g., "Daily at 02:00 UTC via cron" or "Real-time streaming" or "Triggered by API"]

## Failure Handling
| Failure | Recovery |
|---------|----------|
| [e.g., "Source unavailable"] | [e.g., "Retry 3x, then alert ops, preserve yesterday's data"] |
| [e.g., "Partial failure"] | [e.g., "Checkpoint progress, resume from last success"] |
| [e.g., "Bad source data"] | [e.g., "Quarantine invalid records, continue processing"] |

## Acceptance
<!-- What must be true for this pipeline to be complete? -->
- [ ] [e.g., "Data available in destination by SLA (03:00 UTC)"]
- [ ] [e.g., "Output matches expected schema"]
- [ ] [e.g., "Failure scenarios handled per spec"]
- [ ] [e.g., "Monitoring and alerting in place"]
