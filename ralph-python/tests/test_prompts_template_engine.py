from ralph.prompts.template_engine import Template


def test_render_replaces_variables() -> None:
    template = Template("Hello, {{name}}! Welcome.")
    rendered = template.render({"name": "Ada"})
    assert rendered == "Hello, Ada! Welcome."


def test_render_with_partials_composes_content() -> None:
    template = Template("Intro {{> greeting}} Outro")
    rendered = template.render_with_partials(
        {"name": "Ada"}, {"greeting": "Hello, {{name}}"}
    )
    assert rendered == "Intro Hello, Ada Outro"
