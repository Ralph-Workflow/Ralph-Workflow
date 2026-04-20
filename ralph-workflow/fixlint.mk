.PHONY: fix-lint
fix-lint:
	ruff check --fix ralph/ tests/
	ruff format ralph/ tests/
