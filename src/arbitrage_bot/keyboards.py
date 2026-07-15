from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

SHOW_SPREAD_BUTTON = "Показать спред"
CALCULATE_BUTTON = "Рассчитать прибыль"
SUBSCRIBE_BUTTON = "Включить подписку"
UNSUBSCRIBE_BUTTON = "Отключить подписку"


def main_keyboard(subscribed: bool) -> ReplyKeyboardMarkup:
    subscription_button = UNSUBSCRIBE_BUTTON if subscribed else SUBSCRIBE_BUTTON
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=SHOW_SPREAD_BUTTON),
                KeyboardButton(text=CALCULATE_BUTTON),
            ],
            [KeyboardButton(text=subscription_button)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )
