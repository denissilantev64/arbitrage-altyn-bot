# ruff: noqa: RUF001 -- user-facing text is intentionally written in Russian.

from __future__ import annotations

START_TEXT = """<b>Altyn/Rapira spread bot</b>

/spread - показать текущий спред USDT/RUB
/spread 1000000 - посчитать прибыль на сумму в RUB
/unsubscribe - отключить утренние сообщения
/subscribe - включить уведомления

/help - команды бота

Утренний спред приходит автоматически в 09:00 МСК по будням после первого сообщения боту.

В расчете по сумме учитывается комиссия 🇧🇾 Exchange 1.5% при покупке USDT за рубли.

Можно также нажать «Показать спред» или «Обратная связь»."""

HELP_TEXT = """<b>Команды бота</b>

/spread - показать текущий спред USDT/RUB
/spread 1000000 - посчитать прибыль на сумму в RUB
/subscribe - включить утренние сообщения
/unsubscribe - отключить утренние сообщения
/help - показать эту справку"""

AMOUNT_PROMPT = "Введите сумму в RUB, например: <code>1000000</code>."
INVALID_AMOUNT_TEXT = (
    "Укажите сумму в RUB положительным числом с максимум двумя знаками после запятой, "
    "например: <code>/spread 1000000</code>."
)
RATES_UNAVAILABLE_TEXT = "Актуальные курсы сейчас недоступны. Попробуйте через несколько минут."
GENERIC_ERROR_TEXT = "Не удалось выполнить запрос из-за внутренней ошибки. Попробуйте позже."
SUBSCRIBED_TEXT = "Подписка включена"
UNSUBSCRIBED_TEXT = "Подписка отключена"
FEEDBACK_TEXT = "Связаться с поддержкой:"
