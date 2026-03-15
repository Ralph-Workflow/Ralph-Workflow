# Onboarding: [Project Name]

> **How to use this template:** This template helps you quickly get up to speed when joining a new codebase. Use it to structure your exploration and document key findings.

## Goal
[Clear description of what you need to learn about the codebase]

**Tips for a good onboarding goal:**
- ✅ "Understand the user authentication flow and how to add new OAuth providers"
- ✅ "Learn the data processing pipeline to contribute to performance improvements"
- ❌ "Learn the codebase" (too vague)

**EXAMPLE:**
```markdown
## Goal
Understand the payment processing system architecture, key components, and development workflow to be able to implement a new payment provider integration.
```

## Questions to Consider
As you explore the codebase, investigate:

**Architecture:**
- What is the overall system architecture?
- What are the main components and how do they interact?
- What are the key technologies and frameworks used?
- What are the external dependencies and integrations?

**Domain:**
- What problem does this codebase solve?
- Who are the users and what are their use cases?
- What are the core concepts and domain language?
- What are the business rules and constraints?

**Development:**
- How do I set up my local development environment?
- How do I run the tests?
- What is the build and deployment process?
- What are the coding conventions and standards?

**Navigation:**
- Where is the entry point for the application?
- Where is the main business logic?
- How is the code organized (modules, packages, layers)?
- What are the key files and directories?

## Acceptance Checks
- [Can explain the architecture and key components]
- [Can run the application locally]
- [Can run and understand the tests]
- [Can make a simple change and verify it works]
- [Know where to find information when stuck]
- [Understood enough to contribute to the planned work]

## Onboarding Notes

### System Overview
[Your understanding of what this codebase does]

**EXAMPLE:**
```markdown
### System Overview

This is a payment processing platform that:
- Handles payment transactions via multiple providers (Stripe, PayPal, etc.)
- Manages user accounts and subscriptions
- Provides webhooks for payment status notifications
- Admin dashboard for transaction monitoring

Key flows:
1. User initiates payment via checkout page
2. Payment is processed through selected gateway
3. Webhook updates transaction status
4. User receives confirmation
```

### Key Components
[List the main components and their responsibilities]

**EXAMPLE:**
```markdown
### Key Components

**Frontend** (React)
- `src/components/` - UI components
- `src/pages/` - Page routes
- `src/api/` - API client calls

**Backend** (Node.js/Express)
- `src/routes/` - HTTP endpoints
- `src/services/` - Business logic
- `src/models/` - Database models
- `src/middleware/` - Auth, validation

**Payments** (Standalone service)
- `src/gateways/` - Payment provider integrations
- `src/webhooks/` - Webhook handlers
```

### Setup Instructions
[Document how to set up and run the project]

**EXAMPLE:**
```markdown
### Setup Instructions

1. Clone repository
2. Install dependencies:
   ```bash
   bun install
   cd frontend && bun install
   ```
3. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your values
   ```
4. Run database migrations:
   ```bash
   bun run db:migrate
   ```
5. Start development servers:
   ```bash
   bun run dev      # Backend
   bun run dev:fe   # Frontend
   ```

Tests:
```bash
bun test           # All tests
bun test:unit      # Unit tests only
bun test:integration  # Integration tests only
```
```

### Important Patterns and Conventions
[Document key patterns, conventions, and gotchas]

**EXAMPLE:**
```markdown
### Important Patterns and Conventions

**Error Handling:**
- All errors go through `src/utils/errors.js`
- Use `AppError` class for application errors
- Always include error codes for frontend mapping

**Database Access:**
- Never use raw SQL queries
- Use ORM methods from `src/models/`
- Transactions are required for multi-table operations

**Code Style:**
- Use async/await, no callbacks
- Function names use camelCase
- File names use kebab-case
- Max line length: 100 characters
```

### Areas of Interest for My Work
[Focus your exploration on areas relevant to your planned work]

**EXAMPLE:**
```markdown
### Areas of Interest for My Work

**Goal:** Add a new payment provider (Square)

**Key files to understand:**
- `src/gateways/base.js` - Gateway interface
- `src/gateways/stripe.js` - Example implementation
- `src/services/payments.js` - Payment orchestration
- `tests/payments/` - Payment test suite

**Key concepts:**
- Gateway adapter pattern
- Webhook signature verification
- Idempotency keys for duplicate prevention
```

## Code Quality Specifications

**Exploration Strategies:**
- Start high-level: understand the big picture before diving into details
- Follow the flow: trace a request from entry to exit
- Read tests: they often document expected behavior
- Draw diagrams: visualize components and their relationships
- Take notes: document what you learn for future reference

**Asking Good Questions:**
- Be specific about what you're trying to understand
- Show what you've already tried or investigated
- Provide context about what you're working on
- Ask about reasoning, not just mechanics

**Onboarding Best Practices:**
- Set up your environment as early as possible
- Make a small change to verify your setup works
- Read the existing documentation before asking
- Look at recent commits to understand ongoing work
- Pair with someone experienced for complex areas
