# vfs-monitor

[![CI](https://github.com/North-web-dev/vfs-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/North-web-dev/vfs-monitor/actions/workflows/ci.yml) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Release](https://img.shields.io/github/v/release/North-web-dev/vfs-monitor?sort=semver)](https://github.com/North-web-dev/vfs-monitor/releases)


Cluster-aware monitor for VFS Global appointment slots. It polls the
`lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable` endpoint on behalf of
a pool of accounts and sends a Telegram alert when a date opens up.

It is designed to run across several independent nodes at once — each node holds
its own subset of accounts, uses its own set of residential proxies, and runs on
its own cadence. If one node dies, the others keep watching the same categories.
Alerts are deduplicated by slot date.

---

## How it works

Each node reaches the API through its own residential proxy (country pinned to
the visa center's region). `CheckIsSlotAvailable` sits behind a Cloudflare
challenge; it is solved up front via Capsolver `AntiCloudflareTask`, and the
resulting `cf_clearance` is cached and reused until it expires. A VFS login
returns an opaque access token (not a JWT) valid for ~6 hours; when it expires,
`auto_login.py` performs a fresh login through a Turnstile challenge (Capsolver
`AntiTurnstileTaskProxyLess`).

```
             registered_accounts.json   (shared account registry)
                    |          |          |
                node-a      node-b     node-c
                    |          |          |
              accounts.yaml  ...        ...   (per-node slice of the pool)
              proxies.txt    ...        ...   (per-node residential set)
                    |          |          |
              multi_auto_run  ...        ...  (main poller)
              datewatch       ...        ...  (date-reading daemon)
              watchdog        ...        ...  (health check)
                    |          |          |
                    +----------+----------+
                               |
                        Telegram (broadcast)
```

---

## Components

### Main processes (run as systemd services)

| File | Role |
|---|---|
| `multi_auto_run.py` | Main poller. Keeps a pool of live sessions, sweeps all categories in turn, rotates tokens, handles 429 without a relogin, and rotates the proxy sid on a `cf_clearance` miss. |
| `datewatch.py` | Lightweight daemon that runs alongside the pool. Does a fresh clean login (own sid) and reads `earliestDate` for every category, so a date is not missed even when the main pool is soft-blocked. Alerts to Telegram on new dates. |
| `authed_hunter.py` | Single-account hunter. Kept as a fallback / one-account test run. |
| `watchdog.sh` / `dw_watchdog.py` | Node health check. Restarts a crashed service and alerts if the node goes blind for more than N minutes. |

### Login / sessions

| File | Role |
|---|---|
| `auto_login.py` | Full login flow: Capsolver `AntiCloudflareTask` → `cf_clearance` → `POST /user/login` with Turnstile → token + session. Persists `session_<email>.json`. |
| `jwt_pool.py` | Session-pool manager — slices the pool across nodes, picks the coldest account by `last_login`, applies cooldowns to 429'd accounts. |
| `direct_api.py` | Thin wrappers over the VFS HTTP endpoints — `CheckIsSlotAvailable`, `calendar`, `slot`, `schedule`. |
| `chrome_session_extract.py` | Utility to pull cookies/session out of a real Chromium profile (debugging). |
| `cloak_solver.py` | Minimal shim for the legacy solver interface. |

### Proxies

| File | Role |
|---|---|
| `proxy_pool.py` | Reads `proxies.txt`, hands out a random / sticky proxy. |
| `rotate_sids.py` | Rotates the sticky session id in a residential proxy (new IP, same provider). |

### Categories and slots

| File | Role |
|---|---|
| `slot_hunter.py` | Thin single-category poller (used as a library). |
| `instant_book.py` | Optional instant booking of a caught slot. Six VFS API steps: CheckSlot → getApplicants → register → calendar → timeslot → schedule. Dry-run by default. |

### Telegram

| File | Role |
|---|---|
| `tg_notifier.py` | Broadcasts an alert to all bot subscribers (`data/tg_subscribers.json`). |
| `tg_bot_listener.py` | Bot update poller — handles `/start` (subscribe), `/pool` (status), `/help`. |

### Other

| File | Role |
|---|---|
| `contentful_poller.py` | Watches the VFS page on the Contentful CMS in parallel (news / policy changes). |

---

## Categories

The monitored categories are visa-appointment codes for a given
origin→destination pairing. The list is defined in `multi_auto_run.py` and
`datewatch.py`. One category is used as a **control**: its slots are reliably
available, so if the code stops seeing them the session is dead or the node has
gone blind.

Request parameters are fixed (`countryCode`, `missionCode`, `vacCode`,
`visaCategoryCode`). The `cfmlift: mobile` header bypasses the Cloudflare rule
that would otherwise require a JS challenge on `/appointment/*`.

---

## Dependencies

- Python 3.10+
- `curl_cffi` — Chrome 131 TLS impersonation (needed to pass CF fingerprinting;
  plain `requests` is blocked)
- `playwright` + `chromium` — only for `auto_login.py` and
  `chrome_session_extract.py` (the rest of the flow is browserless)
- `pyyaml`, `loguru`, `aiohttp`

---

## Configuration

### Environment variables

| Variable | Role |
|---|---|
| `CAPSOLVER_KEY` | Capsolver API key for `AntiCloudflareTask` + `AntiTurnstileTaskProxyLess`. |
| `TG_TOKEN` | Telegram bot token for alerts. |
| `VFS_PROXY` | Default proxy (residential, http). Overridden by a proxy from `proxies.txt` when present. |
| `OWNER_CHAT_ID` | Owner chat id — receives every alert regardless of subscriptions. |
| `NODE_NAME` | Node name — used in state files and alert text. |
| `NSESS`, `CYCLE`, `TARGET_LIVE`, `POLL_GAP`, `JWT_TTL` | Tuning knobs. See `datewatch.py` and `multi_auto_run.py`. |

### Config files (create before running)

```
config/.env             # CAPSOLVER_KEY=..., TG_TOKEN=...
config/proxies.txt      # one per line: http://user:pass@host:port or host:port:user:pass
config/accounts.yaml    # accounts with their sticky proxy
```

Templates:

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

## Running

### systemd

```ini
# /etc/systemd/system/vfs-multi.service
[Unit]
Description=VFS multi-account slot poller
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vfs-monitor
EnvironmentFile=/opt/vfs-monitor/config/.env
Environment=NODE_NAME=a
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
Environment=NODE_NAME=a
Environment=NSESS=5
Environment=CYCLE=8
ExecStart=/usr/bin/python3 /opt/vfs-monitor/datewatch.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```
systemctl daemon-reload
systemctl enable --now vfs-multi vfs-datewatch
journalctl -fu vfs-multi -u vfs-datewatch
```

### Manual

```
python3 multi_auto_run.py --interval 30
python3 datewatch.py
python3 authed_hunter.py --email <email> --password <pw> --proxy <url>
```

---

## State

| File | Contents |
|---|---|
| `data/registered_accounts.json` | Shared registry of registered accounts (email, password, activation status, jwt_ok flag). |
| `data/session_<email>.json` | Cached account session (access token, fingerprint, proxy sid). Reused until expiry. |
| `data/pool_state_<node>.json` | Node snapshot for the watchdog: live accounts, cooldowns, category statuses. |
| `data/datewatch_<node>.json` | Last seen dates per category, heartbeat. |
| `data/cf_cache.json` | Cached `cf_clearance` cookies — reused until TTL. |
| `data/tg_subscribers.json` | Bot subscribers (`/start`). |

---

## Tuning

Main constants live in `multi_auto_run.py`:

- `TARGET_LIVE` — how many live sessions to hold (low = gentle/cheap, high =
  aggressive/more coverage).
- `POLL_GAP` — seconds between polls of one category on one node. Lower is
  faster to catch but raises the chance of a per-IP rate limit (429201).
- `MIN_LOGIN_GAP` — anti-burst: a node logs in at most one account per N seconds.
- `JWT_TTL` — after how many seconds a token is treated as suspiciously old.
- `LOGIN_FAIL_LIMIT` + `LOGIN_FREEZE` — after N consecutive login failures,
  freeze the node's logins for M seconds (protects the Capsolver balance when an
  IP is CF-blocked).
- `CF429_ROTATE` — after N consecutive 429201 (CF per-IP limit) an account is
  parked and its sid rotated to a fresh IP.

---

## Notes and limitations

- **VFS issues no refresh token**: an expired access token means a full relogin
  through Turnstile. Each solve costs a fraction of a cent — a short `JWT_TTL`
  plus frequent restarts runs the bill up quickly.
- **429001 — per-account rate limit** (by email), not by IP. Clears in a few
  hours; a proxy change does not help — use a different account.
- **429201 — per-IP rate limit on CheckSlot** (CF). Transient — clears with a
  pause or a new sticky session id.
- **Session soft-block**: VFS can return HTTP 200 with an empty slot list when
  five categories are polled at once — the session is flagged. Mitigated in
  `datewatch.py`: the control category must always return a date; if it is
  empty, the session is burnt and a relogin is triggered.
- **Slot lifetime ranges from seconds to hours.** Monthly drops persist; single
  near-term slots vanish in seconds.

---

## Disclaimer

This project is published for **educational and research purposes** — to
document a resilient, cluster-based approach to polling a rate-limited,
bot-protected API. It is provided **as is, without warranty of any kind**.

Interacting with a third-party service through automation may violate that
service's Terms of Service. **You are solely responsible** for how you use this
code, for holding any account credentials lawfully, for obtaining any required
authorization, and for complying with all applicable laws and terms. The authors
accept **no liability** for any use, misuse, damages, account actions, or losses
arising from it. Use at your own risk.

## License

MIT — see [LICENSE](LICENSE).
