from __future__ import annotations

import pytest

from ralph.mcp.artifacts.product_spec import (
    ProductSpecValidationError,
    normalize_product_spec_content,
)


class TestNormalizeProductSpecContent:
    def test_normalizer_accepts_valid_payload_with_all_fields(self) -> None:
        payload = {
            "title": "Test Title",
            "scope": "Test scope description",
            "goals": ["Goal 1", "Goal 2"],
            "users": ["User 1", "User 2"],
            "constraints": ["Constraint 1"],
            "success_criteria": ["Criterion 1", "Criterion 2"],
            "product_behavior": ["Behavior 1"],
            "ux_ui_requirements": ["UX 1"],
            "scope_boundaries": ["Boundary 1"],
            "open_questions": ["Question 1"],
        }
        result = normalize_product_spec_content(payload)
        assert result["title"] == "Test Title"
        assert result["scope"] == "Test scope description"
        assert result["goals"] == ["Goal 1", "Goal 2"]
        assert result["users"] == ["User 1", "User 2"]
        assert result["constraints"] == ["Constraint 1"]
        assert result["success_criteria"] == ["Criterion 1", "Criterion 2"]
        assert result["product_behavior"] == ["Behavior 1"]
        assert result["ux_ui_requirements"] == ["UX 1"]
        assert result["scope_boundaries"] == ["Boundary 1"]
        assert result["open_questions"] == ["Question 1"]

    def test_normalizer_accepts_valid_payload_with_required_fields_only(self) -> None:
        payload = {
            "title": "Minimal Title",
            "scope": "Minimal scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        result = normalize_product_spec_content(payload)
        assert result["title"] == "Minimal Title"
        assert result["constraints"] == []

    def test_normalizer_raises_on_missing_title(self) -> None:
        payload = {
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)

    def test_normalizer_raises_on_missing_scope(self) -> None:
        payload = {
            "title": "Test Title",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)

    def test_normalizer_raises_on_missing_goals(self) -> None:
        payload = {
            "title": "Test Title",
            "scope": "Test scope",
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)

    def test_normalizer_raises_on_missing_users(self) -> None:
        payload = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "success_criteria": ["Criterion 1"],
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)

    def test_normalizer_raises_on_missing_success_criteria(self) -> None:
        payload = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)

    def test_normalizer_raises_on_extra_fields(self) -> None:
        payload = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
            "unknown_field": "This should cause an error",
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)

    def test_normalizer_raises_on_empty_goals_list(self) -> None:
        payload = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": [],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)

    def test_normalizer_raises_on_empty_users_list(self) -> None:
        payload = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": [],
            "success_criteria": ["Criterion 1"],
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)

    def test_normalizer_raises_on_empty_success_criteria_list(self) -> None:
        payload = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": [],
        }
        with pytest.raises(ProductSpecValidationError):
            normalize_product_spec_content(payload)
