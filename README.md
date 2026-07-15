# Altyn/Rapira spread bot

Telegram-бот для мониторинга направления `Altyn -> Rapira` по паре USDT/RUB. Раз в минуту бот запрашивает у Altyn персональную котировку покупки USDT на эталонную сумму 1 000 000 RUB, получает текущий стакан и taker-комиссию Rapira, а затем сохраняет полный проверенный снимок в SQLite. Результат доступен по запросу и автоматически отправляется подписчикам в 09:00 МСК по будням.

## Команды и меню

- `/start` — зарегистрировать пользователя, включить утреннюю подписку при первом обращении и показать меню.
- `/spread` — показать текущий спред `Altyn -> Rapira` по эталонной сумме 1 000 000 RUB.
- `/spread 1000000` — рассчитать результат для указанной суммы в RUB по последнему сохранённому минутному снимку.
- `/subscribe`, `/unsubscribe` — изменить состояние утренней подписки.
- `/help` — показать команды бота.

Постоянное меню содержит «Показать спред», «Рассчитать прибыль», динамическую кнопку подписки и одну кнопку «Поддержка». Она показывает ссылку на Telegram-аккаунт поддержки из `SUPPORT_URL`.

## Финансовая модель

Все цены, комиссии и результаты рассчитываются через `Decimal`.

- Раз в минуту Altyn запрашивается приватным `GET https://api.lk.altyn.one/website/arbitrage-rate/?amount_rub=1000000.00` с обязательным заголовком `X-Arbitrage-Token`. Токен берётся только из `ALTYN_ARBITRAGE_TOKEN` и не включается в URL или логи.
- `rate` из ответа Altyn — цена покупки USDT за RUB для запрошенной суммы. Персональная комиссия клиента Altyn уже включена в этот курс, поэтому отдельные проценты комиссии Altyn не настраиваются и повторно не вычитаются.
- Новый API Altyn не предоставляет курс продажи USDT, поэтому обратное направление `Rapira -> Altyn` отключено.
- Rapira bid и ask берутся из текущего публичного стакана USDT/RUB. При каждом минутном обновлении taker-комиссия уровня 0 читается из `market/fee/page-query`; отсутствующие цены или комиссия не заменяются последней сделкой либо нулём.

Обычный `/spread` и утренняя рассылка используют сохранённую котировку Altyn на 1 000 000 RUB. Показатель «спред без комиссии» уже содержит персональную комиссию Altyn внутри курса, но не учитывает taker-комиссию Rapira; «спред с комиссией» дополнительно учитывает актуальную taker-комиссию продажи на Rapira. Фиксированная сетевая комиссия Altyn в этих сообщениях не применяется.

Для `/spread n` бот не обращается к внешним API. Он берёт из последнего сохранённого снимка курс Altyn для эталонной суммы 1 000 000 RUB, фиксированную `network_fee_usdt`, bid Rapira и её taker-комиссию. Из купленного количества USDT один раз вычитается сетевая комиссия, после чего рассчитываются продажа и прибыль. Такой расчёт индикативный: он не учитывает возможную зависимость курса Altyn от суммы, глубину стакана, проскальзывание, изменение цены во время перевода или фактическое исполнение сделок.

## Ошибки и свежесть данных

- Ответы внешних API строго проверяются: отсутствующие стороны рынка, комиссия, неверная пара, неположительные цены, HTTP/application errors и неожиданные схемы считаются ошибкой.
- Результат каждой попытки обновления сохраняется отдельно: после ошибки внешнего API предыдущий снимок не подставляется как текущий.
- Эталонный снимок на 1 000 000 RUB обновляется раз в минуту и считается устаревшим через две минуты.
- Все пользовательские расчёты спреда читают только SQLite и никогда не инициируют запросы к Altyn или Rapira.
- Пока свежего полного снимка нет, пользователь получает безопасное сообщение о недоступности курсов.
- Если утренняя рассылка ожидает свежий снимок или незавершенную доставку, готовность проверяется раз в минуту независимо от минутного сбора курсов.
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

Для полноценного локального запуска с обращением к внешнему API заполните четыре обязательные runtime-переменные отдельными локальными или dev-значениями; в частности, для `ALTYN_ARBITRAGE_TOKEN` нужен отдельный dev-токен из 64 строчных шестнадцатеричных символов. Реальный production-токен нельзя переносить в workstation `.env`: он хранится только в `/etc/arbitrage-altyn-bot/bot.env`, а в `.env.example` остаётся placeholder. Значения `SERVER_*` приложению не нужны и используются только оператором при развертывании. Затем запустите:

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
ALTYN_ARBITRAGE_TOKEN=<64 lowercase hexadecimal characters>
SUPPORT_URL=https://t.me/manager_altyn_bot
```

Это четыре обязательные runtime-переменные. `ALTYN_ARBITRAGE_TOKEN` является секретом приватного API и должен находиться только на production-сервере в файле с правами `0600`; его нельзя коммитить, передавать в URL или копировать в workstation `.env`.

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

Создайте `/etc/arbitrage-altyn-bot/bot.env` только с четырьмя runtime-переменными из примера выше. Не копируйте workstation `.env`, потому что он также может содержать реквизиты доступа к серверу. Значение `ALTYN_ARBITRAGE_TOKEN` добавьте непосредственно на сервере и не выводите в терминал или журнал.

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

`StateDirectory` в unit-файле создаёт `/var/lib/arbitrage-altyn-bot` с нужными правами. Текущая схема SQLite имеет версию 2. При обновлении базы версии 1 несовместимые старые котировки и состояние их последнего обновления транзакционно копируются в `rate_snapshots_v1_archive` и `rate_refresh_state_v1_archive`; они сохраняются для аудита, но никогда не интерпретируются как актуальные котировки новой модели. Пользователи и история рассылок остаются рабочими, а новая таблица текущих котировок начинает наполняться снимками версии 2.

При непрерывно успешных минутных обновлениях таблица истории растёт примерно на 525 600 снимков в год, поэтому свободное место нужно мониторить. Для согласованной резервной копии используйте SQLite backup API либо временно остановите сервис и копируйте базу вместе с WAL/SHM-файлами; копирование только основного файла работающей WAL-базы некорректно.
