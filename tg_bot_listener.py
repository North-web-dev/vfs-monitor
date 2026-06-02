

import json
import time
import urllib.request
import sys
from pathlib import Path

sys.path.insert(0, "/opt/vfs-monitor")
from tg_notifier import (
    TG_TOKEN, load_subscribers, add_subscriber, remove_subscriber, send_to,
)

ROOT = Path("/opt/vfs-monitor")
DATA = ROOT / "data"
OFFSET_FILE = DATA / "tg_bot_offset.json"

def LOG(*a):
    print("[" + time.strftime("%H:%M:%S") + "]", "tg-bot:", *a, flush=True)

def load_offset():
    if OFFSET_FILE.exists():
        try:
            return json.loads(OFFSET_FILE.read_text()).get("offset", 0)
        except Exception:
            pass
    return 0

def save_offset(offset):
    OFFSET_FILE.write_text(json.dumps({"offset": offset}))

def get_updates(offset, timeout=30):
    try:
        url = "https://api.telegram.org/bot" + TG_TOKEN + "/getUpdates?offset=" + str(offset) + "&timeout=" + str(timeout)
        with urllib.request.urlopen(url, timeout=timeout + 5) as r:
            return json.loads(r.read())
    except Exception as e:
        LOG("getUpdates fail: " + str(e))
        return None

def get_hunter_state():
    p = DATA / "hunter_state.json"
    if not p.exists():
        return "📊 Hunter status\n⏳ starting up... state file will appear within 1 min"
    try:
        s = json.loads(p.read_text())
        cycles = s.get("cycles", 0)
        last_state = s.get("last_state", {})
        boot = s.get("boot_at", 0)
        uptime_h = (time.time() - boot) / 3600 if boot else 0
        block = s.get("block_streak", 0)
        lines = ["📊 Hunter status"]
        lines.append("uptime: " + str(round(uptime_h, 1)) + "h, cycles: " + str(cycles))
        if last_state:
            for cat, st in last_state.items():
                emoji = "🟢" if st == "no-slots" else "🎯" if st == "SLOT" else "🟡"
                lines.append("  " + emoji + " " + cat + ": " + st)
        else:
            lines.append("  ⏳ no readings yet")
        lines.append("CF block streak: " + str(block))
        return "\n".join(lines)
    except Exception as e:
        return "📊 Hunter status\n⚠️ read error: " + str(e)[:80]

def handle_update(upd):
    msg = upd.get("message") or upd.get("edited_message") or {}
    if not msg:
        return
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return
    username = chat.get("username") or chat.get("first_name") or ""
    text = (msg.get("text") or "").strip()

    if text.startswith("/start"):
        if add_subscriber(chat_id, username):
            send_to(chat_id,
                "🟢 Подписан на VFS slot alerts!\n\n"
                "Канал @tabrizulkinbot будет присылать:\n"
                "  🎯 SLOT OPEN — найден слот в API\n"
                "  📡 CMS announcements — VFS опубликовала news\n"
                "  🟡 CF flags / system events\n\n"
                "Команды:\n"
                "  /status — текущее состояние мониторинга\n"
                "  /stop — отписаться")
            LOG("subscribed: " + str(chat_id) + " @" + username)
        else:
            send_to(chat_id, "Вы уже подписаны на алёрты.")
    elif text.startswith("/stop"):
        if remove_subscriber(chat_id):
            send_to(chat_id, "🔴 Отписан от алёртов. Чтобы вернуться — /start")
            LOG("unsubscribed: " + str(chat_id))
        else:
            send_to(chat_id, "Вы и так не подписаны.")
    elif text.startswith("/status"):
        send_to(chat_id, get_hunter_state())
    elif text.startswith("/list"):
        subs = load_subscribers()
        msg_t = "Подписчиков: " + str(len(subs)) + "\n"
        for s in subs[:20]:
            msg_t += "  " + str(s["chat_id"]) + " @" + s.get("username", "") + "\n"
        send_to(chat_id, msg_t)
    else:
        send_to(chat_id,
            "Команды:\n"
            "  /start — подписаться на slot alerts\n"
            "  /stop — отписаться\n"
            "  /status — состояние мониторинга\n"
            "  /list — список подписчиков")

def main():
    LOG("=== tg_bot_listener start ===")
    offset = load_offset()
    while True:
        try:
            data = get_updates(offset)
            if not data or not data.get("ok"):
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                handle_update(upd)
                offset = max(offset, upd["update_id"] + 1)
            save_offset(offset)
        except KeyboardInterrupt:
            LOG("interrupted")
            save_offset(offset)
            return
        except Exception as e:
            LOG("loop err: " + str(e))
            time.sleep(5)

if __name__ == "__main__":
    main()
