from __future__ import annotations

import pytest

from arbitrage_bot.keyboards import (
    CALCULATE_BUTTON,
    SHOW_SPREAD_BUTTON,
    SUBSCRIBE_BUTTON,
    UNSUBSCRIBE_BUTTON,
    main_keyboard,
)


def _button_texts(subscribed: bool) -> list[str]:
    keyboard = main_keyboard(subscribed)
    return [button.text for row in keyboard.keyboard for button in row]


@pytest.mark.parametrize(
    ("subscribed", "visible_subscription_button", "hidden_subscription_button"),
    [
        (False, SUBSCRIBE_BUTTON, UNSUBSCRIBE_BUTTON),
        (True, UNSUBSCRIBE_BUTTON, SUBSCRIBE_BUTTON),
    ],
)
def test_main_keyboard_has_all_actions_and_dynamic_subscription_button(
    subscribed: bool,
    visible_subscription_button: str,
    hidden_subscription_button: str,
) -> None:
    keyboard = main_keyboard(subscribed)
    texts = _button_texts(subscribed)

    assert texts == [
        SHOW_SPREAD_BUTTON,
        CALCULATE_BUTTON,
        visible_subscription_button,
    ]
    assert hidden_subscription_button not in texts
    assert keyboard.resize_keyboard is True
    assert keyboard.is_persistent is True
