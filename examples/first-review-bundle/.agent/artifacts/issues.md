---
type: issues
status: issues_found
---

## Summary

- [SUM-1] The first pass rejected empty strings but still accepted whitespace-only project names.

## Issues

- [I-1] cli/create.py | medium | Whitespace-only names reach file creation because validation runs before normalization.

## What Came Up Short

- [W-1] The first pass did not cover input that becomes empty after trimming.

## How To Fix

- [FIX-1] Normalize the name before validation, add a whitespace-only regression case, and rerun pytest tests/test_create.py.
