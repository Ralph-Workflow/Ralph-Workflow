from __future__ import annotations

from ralph.agents import invoke as invoke_module


def test_extract_choice_menu_state_parses_numbered_modal_options() -> None:
    screen = """
    Enable auto mode?

    \u276f 1. Yes, and make it my default mode
      2. Yes, enable auto mode
      3. No, exit

    Enter to confirm · Esc to cancel
    """

    state = invoke_module._extract_choice_menu_state(screen)

    assert state is not None
    assert state.prompt == "Enable auto mode?"
    assert state.selected_index == 0
    assert [option.label for option in state.options] == [
        "Yes, and make it my default mode",
        "Yes, enable auto mode",
        "No, exit",
    ]


def test_plan_menu_response_prefers_non_persistent_affirmative_option() -> None:
    screen = """
    Enable auto mode?

    \u276f 1. Yes, and make it my default mode
      2. Yes, enable auto mode
      3. No, exit

    Enter to confirm · Esc to cancel
    """

    response = invoke_module._plan_choice_menu_response(screen)

    assert response == "\x1b[B\r"


def test_plan_menu_response_confirms_selected_affirmative_option() -> None:
    screen = """
    Enable auto mode?

      1. Yes, and make it my default mode
    \u276f 2. Yes, enable auto mode
      3. No, exit

    Enter to confirm · Esc to cancel
    """

    response = invoke_module._plan_choice_menu_response(screen)

    assert response == "\r"


def test_interactive_auto_response_handles_menu_snapshot_without_prompt_line() -> None:
    screen = """
    \u276f 1. Yes, and make it my default mode
      2. Yes, enable auto mode
      3. No, exit

    Enter to confirm · Esc to cancel
    """

    response = invoke_module._interactive_auto_response_for_prompt(
        screen,
        auto_mode_prompt_seen=False,
    )

    assert response == "\x1b[B\r"
