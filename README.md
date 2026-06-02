# vfs-monitor

Кластерный монитор слотов записи в визовый центр VFS Global. Опрашивает API
`lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable` от имени пула
аккаунтов; при появлении свободных дат отправляет уведомление в Telegram.

Разворачивается на нескольких независимых нодах одновременно — каждая нода
держит свой подпул аккаунтов, использует свой набор резидентных прокси и
работает в собственном ритме. Если одна нода падает, две другие продолжают
ловить слоты с теми же категориями. Алёрты дедуплицируются по дате слота.

---

## Архитектура

```
┌─────────────────────────────────────────────────────────┐
│  registered_accounts.json   ←  единый реестр аккаунтов  │
└──────────┬────────────┬─────────────────┬───────────────┘
           │            │                 │
      node-master   node-fi           node-de
           │            │                 │
    accounts.yaml  accounts.yaml    accounts.yaml      (своя нарезка пула)
    proxies.txt    proxies.txt      proxies.txt        (свой набор residential)
           │            │                 │
    multi_auto_run  multi_auto_run  multi_auto_run    (главный поллер)
    datewatch       datewatch       datewatch         (демон чтения дат)
    watchdog        watchdog        watchdog          (health-check)
           │            │                 │
           └────────────┴────────┬────────┘
                                 ▼
                         Telegram (broadcast)
```

Каждая нода ходит на API через свой residential прокси (страна = UZ для
визового центра в Ташкенте). На запрос к `CheckIsSlotAvailable` Cloudflare
ставит challenge — он решается заранее через Capsolver `AntiCloudflareTask`,
полученный `cf_clearance` кешируется и переиспользуется до истечения. Логин
в VFS возвращает opaque-access-token (не JWT) на ~6 часов; протух — релогин
через `auto_login.py` с прохождением Turnstile (Capsolver
`AntiTurnstileTaskProxyLess`).

---

## Файлы

### Главные процессы (запускаются как systemd-сервисы)

| Файл | Назначение |
|---|---|
| `multi_auto_run.py` | Главный поллер. Держит пул живых сессий, опрашивает все категории по очереди, ротирует токены, обрабатывает 429 без релогина, при `cf_clearance`-флаге ротирует sid прокси. |
| `datewatch.py` | Лёгкий демон параллельно с пулом. Делает свежий чистый логин (свой sid) и читает `earliestDate` всех категорий — нужен чтобы не пропустить дату даже если основной пул софт-блокнут. Алёртит в TG про новые даты. |
| `authed_hunter.py` | Старый одиночный хантер (на 1 акк). Оставлен как fallback / тестовый прогон одного аккаунта. |
| `watchdog.sh` / `dw_watchdog.py` | Health-check ноды. Перезапускает упавший сервис, шлёт алёрт если нода ослепла больше N минут. |

### Логин/сессии

| Файл | Назначение |
|---|---|
| `auto_login.py` | Полный логин-флоу: Capsolver `AntiCloudflareTask` → `cf_clearance` → POST `/user/login` с Turnstile → token + session. Сохраняет `session_<email>.json`. |
| `jwt_pool.py` | Менеджер пула сессий — нарезка по нодам, выбор холодного аккаунта по `last_login`, кулдауны на 429-аккаунты. |
| `direct_api.py` | Тонкие обёртки над HTTP-эндпоинтами VFS — `CheckIsSlotAvailable`, `calendar`, `slot`, `schedule`. |
| `chrome_session_extract.py` | Утилита: вытащить cookies/session из реального Chromium-профиля (отладка). |
| `cloak_solver.py` | Минимальный шим под старый интерфейс solver'а. |

### Прокси

| Файл | Назначение |
|---|---|
| `proxy_pool.py` | Чтение `proxies.txt`, выдача случайной/sticky прокси. |
| `rotate_sids.py` | Ротация sticky-session-id в residential прокси (новый IP без смены провайдера). |

### Категории и слоты

| Файл | Назначение |
|---|---|
| `slot_hunter.py` | Тонкий обёрточный поллер одной категории (используется как библиотека). |
| `instant_book.py` | Мгновенная бронь поймавшегося слота (опционально). Шесть шагов VFS API: CheckSlot → getApplicants → register → calendar → timeslot → schedule. По умолчанию dry-run. |

### Telegram

| Файл | Назначение |
|---|---|
| `tg_notifier.py` | Бродкаст алёрта всем подписчикам бота (`data/tg_subscribers.json`). |
| `tg_bot_listener.py` | Поллер апдейтов бота — обрабатывает `/start` (подписка), `/pool` (статус), `/help`. |

### Прочее

| Файл | Назначение |
|---|---|
| `contentful_poller.py` | Параллельный мониторинг страницы VFS на Contentful CMS (новости/изменения политики). |

---

## Категории

Коды категорий VFS, которые мониторятся (категория записи на собеседование
по типу визы для конкретной комбинации страна-цель → страна-консульство).
Список зашит в `multi_auto_run.py` и `datewatch.py`:

| Код | Описание |
|---|---|
| `LSHMEDCL` | Work — короткосрочная (UZB → LVA) |
| `LNGWORK`  | Cargo — рабочая длинная (UZB → LVA) |
| `LNGRSDTJK` | Resident (TJK → LVA) |
| `LNGWORKTJK` | Cargo рабочая (TJK → LVA) |
| `LNGSTUD` | Student — используется как **контрольная** категория: студенческие слоты стабильно доступны, если код их перестал видеть — значит сессия мертва или нода ослепла. |

Параметры запроса фиксированы:
`countryCode=uzb`, `missionCode=lva`, `vacCode=TAS` (Ташкент = единственный
хаб, отвечает и на TJK-категории), `visaCategoryCode=<код выше>`. Заголовок
`cfmlift: mobile` обходит CF-правило, требующее JS-челлендж на
`/appointment/*`.

---

## Зависимости

- Python 3.10+
- `curl_cffi` — TLS-импersonation Chrome 131 (нужно для прохождения CF
  fingerprinting; обычный `requests` отбивается)
- `playwright` + `chromium` — только для `auto_login.py` и
  `chrome_session_extract.py` (HTTP-флоу остального работает без браузера)
- `pyyaml`, `loguru`, `aiohttp`

Установка: см. `requirements.txt` (нет в репозитории — соберётся по импортам).

---

## Конфигурация

### Переменные окружения

| Переменная | Назначение |
|---|---|
| `CAPSOLVER_KEY` | API-ключ Capsolver для AntiCloudflareTask + AntiTurnstileTaskProxyLess. |
| `TG_TOKEN` | Bot token Telegram бота для алёртов. |
| `VFS_PROXY` | Дефолтный прокси (residential UZ, http schema). Перебивается прокси из `proxies.txt` при наличии. |
| `OWNER_CHAT_ID` | Главный chat_id владельца — получает все алёрты независимо от подписок. |
| `NODE_NAME` | Имя ноды (`master` / `fi` / `de`) — попадает в state-файлы и в текст алёрта. |
| `NSESS`, `CYCLE`, `TARGET_LIVE`, `POLL_GAP`, `JWT_TTL` | Тюнинг датчика. См. `datewatch.py` и `multi_auto_run.py`. |

### Конфиг-файлы (создать перед запуском)

```
config/.env             # CAPSOLVER_KEY=..., TG_TOKEN=...
config/proxies.txt      # по 1 на строку: http://user:pass@host:port или host:port:user:pass
config/accounts.yaml    # пул аккаунтов с привязкой к sticky-прокси
```

Шаблоны:
```env
# .env
CAPSOLVER_KEY=<your_capsolver_api_key>
TG_TOKEN=<your_telegram_bot_token>
OWNER_CHAT_ID=<your_telegram_chat_id>
```

```yaml
# accounts.yaml
accounts:
  - id: 1
    email: user1@example.com
    password: <pw>
    proxy: http://user:pass@gate.example.com:8080   # sticky residential
  - id: 2
    email: user2@example.com
    password: <pw>
    proxy: http://user:pass@gate.example.com:8080
```

---

## Запуск

### Systemd-сервисы

```ini
# /etc/systemd/system/vfs-multi.service
[Unit]
Description=VFS multi-account slot poller
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vfs-monitor
EnvironmentFile=/opt/vfs-monitor/config/.env
Environment=NODE_NAME=fi
ExecStart=/usr/bin/python3 /opt/vfs-monitor/multi_auto_run.py --interval 30
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/vfs-datewatch.service
[Unit]
Description=VFS date watcher (parallel clean-login probe)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vfs-monitor
EnvironmentFile=/opt/vfs-monitor/config/.env
Environment=NODE_NAME=fi
Environment=NSESS=5
Environment=CYCLE=8
ExecStart=/usr/bin/python3 /opt/vfs-monitor/datewatch.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Затем:
```
systemctl daemon-reload
systemctl enable --now vfs-multi vfs-datewatch
journalctl -fu vfs-multi -u vfs-datewatch
```

### Ручной запуск

```
cd /opt/vfs-monitor
python3 multi_auto_run.py --interval 30
python3 datewatch.py
python3 authed_hunter.py --email <email> --password <pw> --proxy <url>
```

---

## State

| Файл | Содержимое |
|---|---|
| `data/registered_accounts.json` | Общий реестр зарегистрированных аккаунтов (email, password, статус активации, jwt_ok-флаг). |
| `data/session_<email>.json` | Кешированная сессия акка (access-token, fingerprint, sid прокси). Реюзается до истечения. |
| `data/pool_state_<node>.json` | Снапшот ноды для watchdog'а: live-аккаунты, кулдауны, статусы категорий. |
| `data/datewatch_<node>.json` | Последние увиденные даты по категориям, heartbeat. |
| `data/cf_cache.json` | Кеш `cf_clearance` cookies — переиспользуется до TTL. |
| `data/tg_subscribers.json` | Подписчики бота (`/start`). |

---

## Тюнинг

Основные констаны в `multi_auto_run.py`:

- `TARGET_LIVE` — сколько живых сессий держим (5 — гентл, экономный режим;
  50 — агрессивный, больше покрытия)
- `POLL_GAP` — секунд между поллами одной категории на одной ноде. Меньше —
  быстрее ловим, но выше шанс per-IP rate-limit (429201).
- `MIN_LOGIN_GAP` — анти-бурст: нода логинит не чаще 1 акк в N секунд.
- `JWT_TTL` — через сколько секунд считаем токен подозрительно старым.
- `LOGIN_FAIL_LIMIT` + `LOGIN_FREEZE` — после N подряд провалов логина
  замораживаем логин ноды на M секунд (защита от слива Capsolver-кредитов
  при CF-блоке IP).
- `CF429_ROTATE` — после N подряд 429201 (CF per-IP лимит) акк паркуется,
  его sid ротируется на свежий IP.

---

## Известные особенности и ограничения

- **VFS не выдаёт refresh-token**: протух access-token = только полный
  повторный логин через Turnstile. Капча стоит ~$0.0012 за решение — при
  плохой настройке (короткий `JWT_TTL` + частые рестарты) счёт растёт быстро.
- **429001 — per-account rate-limit** (по email), не по IP. Отпускает за
  несколько часов. Не лечится сменой прокси — нужно использовать другой
  аккаунт.
- **429201 — per-IP rate-limit на CheckSlot** (CF). Транзиентный — лечится
  паузой или сменой sticky-session-id.
- **Софт-блок сессии**: VFS может вернуть HTTP 200 с пустым списком слотов
  при бурст-опросе 5 категорий разом — сессия флагнута. Лечение в
  `datewatch.py`: контрольная категория `LNGSTUD` всегда должна возвращать
  дату, если пуста — сессия сожжена, перелогин.
- **Слот живёт от секунд до часов**. Месячные дропы держатся стабильно;
  одиночные слоты на ближайшие даты гаснут за секунды.

---

## Структура каталога

```
vfs-monitor/
├── README.md
├── .gitignore
├── auto_login.py
├── authed_hunter.py
├── chrome_session_extract.py
├── cloak_solver.py
├── contentful_poller.py
├── datewatch.py
├── direct_api.py
├── dw_watchdog.py
├── instant_book.py
├── jwt_pool.py
├── multi_auto_run.py
├── proxy_pool.py
├── rotate_sids.py
├── slot_hunter.py
├── tg_bot_listener.py
├── tg_notifier.py
├── watchdog.sh
├── config/
│   └── csk_pubkey.pem        # публичный ключ для RSA-OAEP шифрования пароля при POST /user/login
└── data/
    └── <state files at runtime>
```
