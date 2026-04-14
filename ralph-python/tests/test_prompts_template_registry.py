import pytest

from ralph.prompts.template_registry import TemplateNotFound, TemplateRegistry


def test_returns_registered_template_content():
    registry = TemplateRegistry()
    registry.register_template("welcome", "Hello, {name}!")

    template = registry.get_template("welcome")

    assert template == "Hello, {name}!"


def test_get_template_raises_when_missing():
    registry = TemplateRegistry()

    with pytest.raises(TemplateNotFound):
        registry.get_template("does_not_exist")
