# Telegram-бот для продажи подписок Remnawave

Этот Telegram-бот предназначен для автоматизации продажи и управления подписками для панели **Remnawave**. Он интегрируется с API Remnawave для управления пользователями и подписками, а также использует различные платежные системы для приема платежей.

## ✨ Ключевые возможности

### Для пользователей:
-   **Регистрация и выбор языка:** Поддержка русского и английского языков.
-   **Просмотр подписки:** Пользователи могут видеть статус своей подписки, дату окончания и ссылку на конфигурацию.
-   **Пробная подписка:** Система пробных подписок для новых пользователей (активируется вручную по кнопке).
-   **Промокоды:** Возможность применять промокоды для получения скидок или бонусных дней.
-   **Реферальная программа:** Пользователи могут приглашать друзей и получать за это бонусные дни подписки.
-   **Оплата:** Поддержка оплаты через YooKassa, CryptoPay, Telegram Stars и Tribute.

### Для администраторов:
-   **Защищенная админ-панель:** Доступ только для администраторов, указанных в `ADMIN_IDS`.
-   **Статистика:** Просмотр статистики использования бота (общее количество пользователей, забаненные, активные подписки), недавние платежи и статус синхронизации с панелью.
-   **Управление пользователями:** Блокировка/разблокировка пользователей, просмотр списка забаненных и детальной информации о пользователе.
-   **Рассылка:** Отправка сообщений всем пользователям, пользователям с активной или истекшей подпиской.
-   **Управление промокодами:** Создание и просмотр промокодов.
-   **Синхронизация с панелью:** Ручной запуск синхронизации пользователей и подписок с панелью Remnawave.
-   **Логи действий:** Просмотр логов всех действий пользователей.
-   **Подарочные кампании (CLI):** выдача бесплатных дней ушедшим пользователям группами по дням, с записью каждого подарка и отчётом. Запускается из контейнера (вручную или ежедневным таймером), не из чата:
    ```bash
    docker exec remnawave-tg-shop python -m bot.cli.gift_campaign plan --code <код> --title "<подпись>" \
        --message-key gift_winback_message --paid-days 10 --trial-days 5 --group-size 200 --seed <число> [--dry-run]
    docker exec remnawave-tg-shop python -m bot.cli.gift_campaign run --code <код> --group <N> [--dry-run]
    docker exec remnawave-tg-shop python -m bot.cli.gift_campaign run --code <код> --next   # для таймера; resume снимает стоп
    docker exec remnawave-tg-shop python -m bot.cli.gift_campaign report --code <код>   # export — то же по каждому пользователю в CSV
    ```
    `plan` фиксирует список на момент запуска: истёкшая подписка, не забанен; сегмент `paid` — был успешный платёж, `trial` — только пробные или бонусные дни. `run` перед выдачей заново проверяет каждого: продлившиеся сами и не-`EXPIRED` в панели пропускаются с причиной. После выдачи срок перечитывается из панели, и сообщение уходит, только если панель его приняла. Повторный `run` той же группы ничего не выдаёт дважды. `add --group 0` добавляет сотрудника для живой проверки.
    `run --next` берёт следующую группу, но не чаще раза в 20 часов. Он сам останавливает кампанию, если панель не приняла продление или не ушло больше 10 % сообщений. После каждой группы пишет сводку в `LOG_CHAT_ID`, а через 14 дней после окончания последнего подарка — итоговый отчёт.

## 🚀 Технологии

-   **Python 3.11**
-   **Aiogram 3.x:** Асинхронный фреймворк для Telegram ботов.
-   **aiohttp:** Для запуска веб-сервера (вебхуки).
-   **SQLAlchemy 2.x & asyncpg:** Асинхронная работа с базой данных PostgreSQL.
-   **YooKassa, aiocryptopay:** SDK для интеграции с платежными системами.
-   **Pydantic:** Для управления настройками из `.env` файла.
-   **Docker & Docker Compose:** Для контейнеризации и развертывания.

## ⚙️ Установка и запуск

### Предварительные требования

-   Установленные Docker и Docker Compose.
-   Рабочая панель Remnawave.
-   Токен Telegram-бота.
-   Данные для подключения к платежным системам (YooKassa, CryptoPay и т.д.).

### Шаги установки

1.  **Клонируйте репозиторий:**
    ```bash
    git clone https://github.com/machka-pasla/remnawave-tg-shop
    cd remnawave-tg-shop
    ```

2.  **Создайте и настройте файл `.env`:**
    Скопируйте `env.example` в `.env` и заполните своими данными.
    ```bash
    cp .env.example .env
    nano .env 
    ```
    Ниже перечислены ключевые переменные.

    <details>
    <summary><b>Основные настройки</b></summary>

    | Переменная | Описание | Пример |
    | --- | --- | --- |
    | `BOT_TOKEN` | **Обязательно.** Токен вашего Telegram-бота. | `1234567890:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` |
    | `ADMIN_IDS` | **Обязательно.** ID администраторов в Telegram через запятую. | `12345678,98765432` |
    | `DEFAULT_LANGUAGE` | Язык по умолчанию для новых пользователей. | `ru` |
    | `SUPPORT_LINK` | (Опционально) Ссылка на поддержку. | `https://t.me/your_support` |
    | `SUBSCRIPTION_MINI_APP_URL` | (Опционально) URL Mini App для показа подписки. | `https://t.me/your_bot/app` |
    </details>

    <details>
    <summary><b>Настройки платежей и вебхуков</b></summary>

    | Переменная | Описание |
    | --- | --- |
    | `WEBHOOK_BASE_URL`| **Обязательно.** Базовый URL для вебхуков, например `https://your.domain.com`. |
    | `WEB_SERVER_HOST` | Хост для веб-сервера. | `0.0.0.0` |
    | `WEB_SERVER_PORT` | Порт для веб-сервера. | `8080` |
    | `YOOKASSA_ENABLED` | Включить/выключить YooKassa (`true`/`false`). |
    | `YOOKASSA_SHOP_ID` | ID вашего магазина в YooKassa. |
    | `YOOKASSA_SECRET_KEY`| Секретный ключ магазина YooKassa. |
    | `CRYPTOPAY_ENABLED` | Включить/выключить CryptoPay (`true`/`false`). |
    | `CRYPTOPAY_TOKEN` | Токен из вашего CryptoPay App. |
    | `STARS_ENABLED` | Включить/выключить Telegram Stars (`true`/`false`). |
    | `TRIBUTE_ENABLED`| Включить/выключить Tribute (`true`/`false`). |
    </details>

    <details>
    <summary><b>Настройки подписок</b></summary>

    Для каждого периода (1, 3, 6, 12 месяцев) можно настроить доступность и цены:
    - `1_MONTH_ENABLED`: `true` или `false`
    - `RUB_PRICE_1_MONTH`: Цена в рублях
    - `STARS_PRICE_1_MONTH`: Цена в Telegram Stars
    - `TRIBUTE_LINK_1_MONTH`: Ссылка для оплаты через Tribute
    Аналогичные переменные есть для `3_MONTHS`, `6_MONTHS`, `12_MONTHS`.
    </details>

    <details>
    <summary><b>Настройки панели Remnawave</b></summary>
    
    | Переменная | Описание |
    | --- | --- |
    | `PANEL_API_URL` | URL API вашей панели Remnawave. |
    | `PANEL_API_KEY` | API ключ для доступа к панели. |
    | `PANEL_WEBHOOK_SECRET`| Секретный ключ для проверки вебхуков от панели. |
    | `USER_SQUAD_UUIDS` | ID отрядов для новых пользователей. |
    | `USER_TRAFFIC_LIMIT_GB`| Лимит трафика в ГБ (0 - безлимит). |
    </gidetails>

    <details>
    <summary><b>Настройки пробного периода</b></summary>

    | Переменная | Описание |
    | --- | --- |
    | `TRIAL_ENABLED` | Включить/выключить пробный период (`true`/`false`). |
    | `TRIAL_DURATION_DAYS`| Длительность пробного периода в днях. |
    | `TRIAL_TRAFFIC_LIMIT_GB`| Лимит трафика для пробного периода в ГБ. |
    </details>

3.  **Запустите контейнеры:**
    ```bash
    docker compose up -d
    ```
    Эта команда скачает образ и запустит сервис в фоновом режиме.

4.  **Настройка вебхуков (Обязательно):**
    Вебхуки являются **обязательным** компонентом для работы бота, так как они используются для получения уведомлений от платежных систем (YooKassa, CryptoPay, Tribute) и панели Remnawave.

    Вам понадобится обратный прокси (например, Nginx) для обработки HTTPS-трафика и перенаправления запросов на контейнер с ботом.

    **Пути для перенаправления:**
    -   `https://<ваш_домен>/webhook/yookassa` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/yookassa`
    -   `https://<ваш_домен>/webhook/cryptopay` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/cryptopay`
    -   `https://<ваш_домен>/webhook/tribute` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/tribute`
    -   `https://<ваш_домен>/webhook/panel` → `http://remnawave-tg-shop:<WEB_SERVER_PORT>/webhook/panel`
    -   **Для Telegram:** Бот автоматически установит вебхук, если в `.env` указан `WEBHOOK_BASE_URL`. Путь будет `https://<ваш_домен>/<BOT_TOKEN>`.

    Где `remnawave-tg-shop` — это имя сервиса из `docker-compose.yml`, а `<WEB_SERVER_PORT>` — порт, указанный в `.env`.

5.  **Просмотр логов:**
    ```bash
    docker compose logs -f remnawave-tg-shop
    ```

## 🐳 Docker

Файлы `Dockerfile` и `docker-compose.yml` уже настроены для сборки и запуска проекта. `docker-compose.yml` использует готовый образ с GitHub Container Registry, но вы можете раскомментировать `build: .` для локальной сборки.

## 📈 Наблюдаемость

-   **Метрики Prometheus** отдаются на `http://<контейнер>:METRICS_PORT/metrics` (по умолчанию `9091`, `0` отключает). Это отдельный aiohttp-сервер: не публикуйте этот порт наружу, публичное приложение на `WEB_SERVER_PORT` метрик не отдаёт. Метрики:
    -   `vpnox_bot_updates_total{kind, handler, outcome}` и `vpnox_bot_update_seconds{kind, handler}` — апдейты по типу (`message`, `callback`, `other`) и обработчику (имя функции-хендлера, `unhandled` если ни один не подошёл);
    -   `vpnox_bot_external_call_seconds{service, op}` и `vpnox_bot_external_call_errors_total{service, op}` — вызовы `panel`, `yookassa`, `cryptopay`, `stars`, `telegram`;
    -   `vpnox_bot_callbacks_unanswered_total{handler}` — нажатия кнопок, на которые хендлер не ответил за 2,5 с; бот отвечает сам, чтобы кнопка не крутилась;
    -   `vpnox_bot_info{version}` — значение `BOT_VERSION`.
-   **Таймауты** внешних вызовов: `PANEL_API_TIMEOUT_SECONDS` (10 с) и `PAYMENT_API_TIMEOUT_SECONDS` (15 с). По истечении пользователь получает обычное сообщение об ошибке.
-   **Продуктовые события** пишутся в таблицу `analytics_events` (`user_id`, `ts`, `name`, `props`, `session_id`; новая сессия после 30 минут без событий). Словарь событий: `start` (`source`: `ref`/`promo`/`ad`/`organic`, `new_user`, `campaign_id`), `trial_activated` (`days`), `section_viewed` (`section`), `subscribe_period_selected` (`months`), `payment_started` (`provider`, `months`, `amount`, `auto_renew`), `payment_succeeded` (то же), `payment_failed` (`provider`, `reason`, `auto_renew`), `referral_shared` (`channel`), `connect_link_opened` (`result`), `autorenew_toggled` (`enabled`, `source`).
-   **Отслеживаемая кнопка подключения:** если задан `CONNECT_LINK_SECRET` (и `WEBHOOK_BASE_URL`), URL-кнопка «Подключиться» ведёт на `/go/connect/<токен>` публичного приложения. Токен подписан HMAC-SHA256 и живёт `CONNECT_LINK_TTL_DAYS` (30 дней); сервер записывает `connect_link_opened` и перенаправляет на актуальную ссылку подписки из панели. Кнопка мини-приложения (`SUBSCRIPTION_MINI_APP_URL`) не меняется, её открытия не отслеживаются. Без секрета кнопка ведёт прямо на ссылку подписки, как раньше.
-   **Тесты:** `pip install -r requirements-dev.txt && python -m pytest`.

## 📁 Структура проекта

```
.
├── bot/
│   ├── filters/          # Пользовательские фильтры Aiogram
│   ├── handlers/         # Обработчики сообщений и колбэков
│   ├── keyboards/        # Клавиатуры
│   ├── middlewares/      # Промежуточные слои (i18n, проверка бана)
│   ├── services/         # Бизнес-логика (платежи, API панели)
│   ├── states/           # Состояния FSM
│   └── main_bot.py       # Основная логика бота
├── config/
│   └── settings.py       # Настройки Pydantic
├── db/
│   ├── dal/              # Слой доступа к данным (DAL)
│   ├── database_setup.py # Настройка БД
│   └── models.py         # Модели SQLAlchemy
├── locales/              # Файлы локализации (ru, en)
├── .env.example          # Пример файла с переменными окружения
├── Dockerfile            # Инструкции для сборки Docker-образа
├── docker-compose.yml    # Файл для оркестрации контейнеров
├── requirements.txt      # Зависимости Python
└── main.py               # Точка входа в приложение
```

## 🔮 Планы на будущее

-   Расширенные типы промокодов (например, скидки в процентах).

## ❤️ Поддержка
- Карты РФ и зарубежные: [Tribute](https://t.me/tribute/app?startapp=dqdg)
- Crypto: `USDT TRC-20 TT3SqBbfU4vYm6SUwUVNZsy278m2xbM4GE`
