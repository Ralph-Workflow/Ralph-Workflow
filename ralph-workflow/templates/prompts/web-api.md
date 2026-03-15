# API: [Name or purpose of the API]

## Purpose
<!-- What problem does this API solve? Who consumes it? -->
[e.g., "Allows the mobile app to manage user accounts"]

## Endpoints
<!-- What endpoints are needed? -->
- [e.g., "POST /users - Create a new user"]
- [e.g., "GET /users/:id - Get user by ID"]
- [e.g., "PUT /users/:id - Update user"]

## Data
<!-- What data is exchanged? -->
- **Input:** [e.g., "User details: name, email, role"]
- **Output:** [e.g., "User object with ID and timestamps"]

## Behavior
<!-- Key behaviors and business rules -->
- [e.g., "Email must be unique across all users"]
- [e.g., "Deleting a user soft-deletes, preserving history"]

## Error Scenarios
<!-- What can go wrong? How should errors appear to the consumer? -->
- [e.g., "Duplicate email → 409 Conflict with message"]
- [e.g., "User not found → 404 with user-friendly message"]
- [e.g., "Invalid input → 400 with field-level errors"]

## Context (optional)
<!-- Authentication, rate limits, or other API-wide concerns -->
[e.g., "Requires JWT auth, rate limited to 100 req/min"]
