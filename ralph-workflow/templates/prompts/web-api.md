# API: [Name or purpose of the API]

## Goal
<!-- What capability does this API provide? Who consumes it? -->
[e.g., "Mobile and web clients can perform user management operations via REST"]

## Resources & Operations
<!-- What resources does this API expose? -->
```
POST   /users          Create user
GET    /users/:id      Get user by ID
PUT    /users/:id      Update user
DELETE /users/:id      Delete user (soft delete)
GET    /users          List users (paginated)
```

## Data Contracts
<!-- What are the request/response shapes? -->
```
User {
  id: string
  email: string (unique)
  name: string
  created_at: timestamp
  updated_at: timestamp
}
```

## Business Rules
<!-- Domain rules the API must enforce -->
- [e.g., "Email must be unique across all users"]
- [e.g., "Delete is soft-delete (sets deleted_at, preserves data)"]
- [e.g., "Name is required, 1-100 characters"]

## Error Handling
<!-- Error response contract -->
- [e.g., "400 Bad Request: validation errors with field-level details"]
- [e.g., "404 Not Found: resource doesn't exist"]
- [e.g., "409 Conflict: uniqueness violation"]
- [e.g., "All errors return { error: string, details?: object }"]

## Non-Functional Requirements (optional)
<!-- Performance, security, operational constraints -->
[e.g., "Rate limit: 100 req/min per client" or "Auth: JWT Bearer token required"]

## Acceptance
<!-- What must be true for this API to be complete? -->
- [ ] [e.g., "All endpoints implemented per spec"]
- [ ] [e.g., "Error responses match contract"]
- [ ] [e.g., "Business rules enforced"]
- [ ] [e.g., "API documentation generated"]
