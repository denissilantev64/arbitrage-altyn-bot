# Altyn/Rapira spread bot

Telegram-бот для мониторинга спреда USDT/RUB между Altyn и Rapira. Бот раз в минуту получает обе стороны котировок, сохраняет проверенный снимок и комиссии в SQLite, выбирает лучшее из двух направлений после комиссий и отправляет результат по запросу или в 09:00 МСК по будням.

## Команды и меню

- `/start` — зарегистрировать пользователя, включить утреннюю подписку при первом обращении и показать меню.
- `/spread` — показать лучшее текущее направление.
- `/spread 1000000` — рассчитать результат на сумму в RUB.
- `/subscribe`, `/unsubscribe` — изменить состояние утренней подписки.
- `/help` — показать команды и ссылку обратной связи.

Постоянное меню содержит «Показать спред», «Рассчитать прибыль», динамическую кнопку подписки, «Помощь» и «Обратная связь». Кнопка «Помощь» и кнопка обратной связи показывают ссылку на Telegram-аккаунт поддержки.

## Финансовая модель

Все значения рассчитываются через `Decimal` и хранятся как RUB за 1 USDT:

- Altyn bid — курс `USDT -> RUB`;
- Altyn ask — `1 / rate(RUB -> USDT)`;
- Rapira bid — максимальная цена BUY из стакана;
- Rapira ask — минимальная цена SELL из стакана;
- комиссия покупки Altyn — 1.5%;
- taker-комиссия Rapira читается из публичной таблицы уровня 0 и сохраняется в каждом снимке.

Для каждого направления рассчитываются спред до комиссий и эффективная доходность после комиссий. Показывается направление с максимальной доходностью после комиссий, в том числе менее убыточное, если оба результата отрицательны. При точном равенстве выбирается `Altyn -> Rapira`.

Расчет по сумме использует текущие лучшие bid/ask. Он является индикативным и не моделирует глубину стакана, проскальзывание, комиссии ввода/вывода и время перевода между площадками.

## Ошибки и свежесть данных

- Ответы внешних API строго проверяются: отсутствующие стороны рынка, комиссия, неверная пара, неположительные цены, HTTP/application errors и неожиданные схемы считаются ошибкой.
- Результат каждой попытки обновления сохраняется отдельно: после ошибки внешнего API предыдущий снимок не подставляется как текущий.
- Даже успешный снимок считается устаревшим через три минуты.
- Пока свежего полного снимка нет, пользователь получает безопасное сообщение о недоступности курсов.
- Ошибки внешних сервисов и Telegram логируются без токенов и внутренних деталей в пользовательском ответе.
- Текст утренней рассылки и состояние доставки каждому адресату сохраняются до отправки; повтор незавершенной рассылки не зависит от получения нового курса.
- При блокировке, удалении или недоступности приватного чата подписка отключается. Неизвестная ошибка доставки оставляет сообщение в очереди для повторной попытки.

## Локальный запуск

Требуется Python 3.11–3.14.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Заполните обязательные `TELEGRAM_BOT_TOKEN`, `DATABASE_PATH` и `SUPPORT_URL`, затем запустите:

```powershell
.venv\Scripts\arbitrage-altyn-bot.exe
```

Проверки:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe src
```

## Production

Целевая раскладка:

- `/opt/arbitrage-altyn-bot` — код и virtualenv;
- `/var/lib/arbitrage-altyn-bot/arbitrage-bot.sqlite3` — SQLite;
- `/etc/arbitrage-altyn-bot/bot.env` — root-readable секреты;
- `deploy/arbitrage-altyn-bot.service` — systemd unit.

Runtime env-файл содержит только:

```dotenv
TELEGRAM_BOT_TOKEN=...
DATABASE_PATH=/var/lib/arbitrage-altyn-bot/arbitrage-bot.sqlite3
SUPPORT_URL=https://t.me/darkvasyak
```

Входящий порт не нужен: бот использует Telegram long polling.

### Первый запуск на Ubuntu

Перед установкой убедитесь, что другой процесс с тем же Telegram-токеном не запущен:

```bash
systemctl status arbitrage-altyn-bot.service --no-pager || true
pgrep -af '[a]rbitrage-altyn-bot|[a]rbitrage_bot' || true
```

Установите системные зависимости и создайте отдельного пользователя:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends ca-certificates git python3-venv
getent group arbitrage-bot >/dev/null || sudo groupadd --system arbitrage-bot
getent passwd arbitrage-bot >/dev/null || sudo useradd --system \
  --gid arbitrage-bot \
  --home-dir /var/lib/arbitrage-altyn-bot --no-create-home \
  --shell /usr/sbin/nologin arbitrage-bot
sudo install -d -o root -g root -m 0700 /etc/arbitrage-altyn-bot
```

Клонируйте опубликованный commit и создайте virtualenv. На малом сервере установка принудительно использует готовые wheels и не собирает нативные зависимости из исходников:

```bash
sudo git clone --branch main --single-branch \
  https://github.com/denissilantev64/arbitrage-altyn-bot.git \
  /opt/arbitrage-altyn-bot
sudo python3 -m venv /opt/arbitrage-altyn-bot/.venv
sudo env PIP_ONLY_BINARY=:all: PIP_NO_CACHE_DIR=1 \
  /opt/arbitrage-altyn-bot/.venv/bin/python -m pip install \
  --upgrade pip setuptools
sudo env PIP_ONLY_BINARY=:all: PIP_NO_CACHE_DIR=1 \
  /opt/arbitrage-altyn-bot/.venv/bin/python -m pip install \
  /opt/arbitrage-altyn-bot
sudo /opt/arbitrage-altyn-bot/.venv/bin/python -m pip check
sudo chown -R root:root /opt/arbitrage-altyn-bot
sudo chmod -R go-w /opt/arbitrage-altyn-bot
```

Создайте `/etc/arbitrage-altyn-bot/bot.env` только с тремя runtime-переменными из примера выше. Не копируйте workstation `.env`, потому что он также содержит реквизиты доступа к серверу.

```bash
sudo install -o root -g root -m 0600 /dev/null \
  /etc/arbitrage-altyn-bot/bot.env
sudoedit /etc/arbitrage-altyn-bot/bot.env
sudo install -o root -g root -m 0644 \
  /opt/arbitrage-altyn-bot/deploy/arbitrage-altyn-bot.service \
  /etc/systemd/system/arbitrage-altyn-bot.service
sudo systemd-analyze verify /etc/systemd/system/arbitrage-altyn-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now arbitrage-altyn-bot.service
sudo systemctl is-active arbitrage-altyn-bot.service
sudo journalctl -u arbitrage-altyn-bot.service -n 100 --no-pager
```

`StateDirectory` в unit-файле создаёт `/var/lib/arbitrage-altyn-bot` с нужными правами. Таблица истории растёт примерно на 525 600 строк в год, поэтому свободное место нужно мониторить. Для согласованной резервной копии используйте SQLite backup API либо временно остановите сервис и копируйте базу вместе с WAL/SHM-файлами; копирование только основного файла работающей WAL-базы некорректно.
