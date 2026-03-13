export interface PromptTemplate {
  id: string;
  label: string;
  description: string;
  content: string;
}

export const PROMPT_TEMPLATES: PromptTemplate[] = [
  {
    id: 'feature',
    label: 'New Feature',
    description: 'Build a net-new capability from a clear product spec.',
    content: `# New Feature

## Goal
<!-- Describe what you want Ralph to build. Be specific about the end-user value. -->

## Acceptance Criteria
<!-- List specific, testable criteria. Each item must be verifiable by running tests or inspecting output. -->
- [ ]
- [ ]
- [ ]

## Context
<!-- Repository section, relevant files, architectural patterns to follow, constraints. -->

## Out of Scope
<!-- Explicitly list what should NOT be changed or added in this task. -->

## Test Requirements
<!-- Describe required test coverage: unit, integration, or e2e. Minimum coverage targets if applicable. -->
`,
  },
  {
    id: 'bugfix',
    label: 'Bug Fix',
    description: 'Diagnose and fix a defect with a clear reproduction case.',
    content: `# Bug Fix

## Bug Description
<!-- Describe the defect clearly. What is broken and how does it manifest? -->

## Steps to Reproduce
1.
2.
3.

## Expected Behavior
<!-- What should happen instead? -->

## Root Cause Hypothesis
<!-- Optional: what do you think is causing this? -->

## Acceptance Criteria
<!-- When is this bug considered fixed? Include regression test requirements. -->
- [ ] Bug no longer reproduces with the reproduction steps above
- [ ] Regression test added to prevent recurrence

## Out of Scope
<!-- Changes not required to fix this bug. State what should remain untouched. -->

## Context
<!-- Relevant files, modules, or components most likely affected. -->
`,
  },
  {
    id: 'refactor',
    label: 'Refactor',
    description: 'Improve internal structure without changing external behavior.',
    content: `# Refactor

## Current State
<!-- Describe what the code looks like now and what problems it causes. -->

## Target State
<!-- Describe the desired structure, patterns, or abstractions after the refactor. -->

## Constraints
<!-- What must NOT change: public API surface, behavior, test contracts, etc. -->

## Acceptance Criteria
<!-- How do we know the refactor is complete and correct? -->
- [ ] All existing tests continue to pass without modification
- [ ] No public API or behavioral changes
- [ ] Code complexity reduced:

## Out of Scope
<!-- What must NOT change: public API surface, behavior, test contracts, etc. -->

## Context
<!-- Files and modules in scope for this refactor. -->
`,
  },
  {
    id: 'test-coverage',
    label: 'Test Coverage',
    description: 'Add missing tests to an existing module or feature.',
    content: `# Test Coverage

## Coverage Target
<!-- Which module, file, or feature needs better test coverage? -->

## Files to Cover
<!-- List specific files or functions that need tests. -->
-

## Test Strategy
<!-- Unit tests, integration tests, property tests? Describe the approach. -->

## Acceptance Criteria
- [ ] Tests cover the happy path for all listed functions
- [ ] Tests cover at least one error/edge case per function
- [ ] No production code changes (tests only)

## Out of Scope
<!-- Production code changes, refactoring, behavior changes. Only tests are in scope. -->

## Context
<!-- Any existing test infrastructure or fixtures to reuse. -->
`,
  },
  {
    id: 'blank',
    label: 'Blank',
    description: 'Start from scratch with an empty template.',
    content: `# Task

## Goal

## Acceptance Criteria
- [ ]

## Context

## Out of Scope
`,
  },
];
