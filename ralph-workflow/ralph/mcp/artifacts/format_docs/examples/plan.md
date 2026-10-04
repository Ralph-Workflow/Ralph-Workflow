---
type: plan
---

## Work Units

- [U-1] Shared request model
  Paths: ralph/api/request_model.py

### [S-1] Define the shared request result
Update the result contract that both independent consumers read.

- [U-2] Command implementation
  Paths: ralph/commands/request.py
  Depends on: U-1

### [S-2] Adopt the shared result in the command
Implement command behavior and run its focused command test.

- [U-3] Operator documentation
  Directories: docs/requests
  Depends on: U-1

### [S-3] Document the shared result
Explain the operator-visible result and build documentation.

- [U-4] Integration verification
  Depends on: U-2, U-3

### [S-4] Verify consumers after fan-in
Run focused command and documentation checks after both consumers complete.
