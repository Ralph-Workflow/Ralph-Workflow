# Development result visual evidence format

Visual evidence is represented by these fields in a development result:

- `design_verdict_id`: the criterion 8 visual verdict identifier.
- `before_capture_set_id`: the capture set reviewed before the change.
- `after_capture_set_id`: the capture set reviewed after the change.
- `cell_handles`: a non-empty list of `ralph://media/{artifact_id}` cell handles.

A visual result is valid only when all four fields are present and every cell handle uses the `ralph://media/` scheme. These fields provide visual proof through `dev_results`; CSS, class, style, and DOM assertions remain implementation evidence rather than design evidence.
