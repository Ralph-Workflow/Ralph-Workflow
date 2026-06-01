# Measurement Hold Release Delivery Route Repair

- Timestamp: 2026-05-26T03:14:34.918660+02:00
- Removed stale route job: 0274bd84-4928-4277-ab44-b735ef91b2db
- Added replacement job: d41cacb2-fd0a-4833-a7a8-57a6e22ec270
- Scheduled run: 2026-05-26T03:05:18.000Z
- Delivery: matrix -> @mistlight_oriroris:matrix.org

Reason:
- The prior one-shot used delivery `announce -> last`, and live scheduler preview showed that it would fail closed because no last-route target was available for this isolated run.
