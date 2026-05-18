# Fix Result

## Applied fix

Updated the validation path to reject names that become empty after trimming surrounding whitespace.

## Re-checks

- `pytest tests/test_create.py`

## Final state

- empty input rejected
- whitespace-only input rejected
- valid-name behavior unchanged
- focused tests passing
