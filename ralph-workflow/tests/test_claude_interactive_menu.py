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


def test_interactive_auto_response_prefers_non_persistent_allow_option() -> None:
    screen = """
    Allow this action?

    \u276f 1. No, cancel
      2. Allow once
      3. Always allow for this session

    Enter to confirm · Esc to cancel
    """

    response = invoke_module._interactive_auto_response_for_prompt(
        screen,
        auto_mode_prompt_seen=False,
    )

    assert response == "\x1b[B\r"


def test_interactive_auto_response_refuses_ambiguous_menu() -> None:
    screen = """
    Choose an action

    \u276f 1. Retry
      2. Skip
      3. Exit

    Enter to confirm · Esc to cancel
    """

    response = invoke_module._interactive_auto_response_for_prompt(
        screen,
        auto_mode_prompt_seen=False,
    )

    assert response is None


def test_permission_prompt_action_message_describes_selected_option() -> None:
    screen = """
    Allow this action?

    \u276f 1. No, cancel
      2. Allow once
      3. Always allow for this session

    Enter to confirm · Esc to cancel
    """

    message = invoke_module._permission_prompt_action_message(
        screen,
        auto_mode_prompt_seen=False,
    )

    assert message is not None
    assert "Allow once" in message
    assert "Allow this action?" in message
