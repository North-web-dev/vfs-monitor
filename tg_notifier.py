import os

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path("/opt/vfs-monitor")
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
SUBSCRIBERS_FILE = DATA / "tg_subscribers.json"

TG_TOKEN = os.getenv("TG_TOKEN", "")

                                             
DEFAULT_SUBSCRIBERS = [{"chat_id": int(os.getenv("OWNER_CHAT_ID", "0")), "username": "operator", "added_at": int(time.time())}]

def load_subscribers():
    if SUBSCRIBERS_FILE.exists():
        try:
            data = json.loads(SUBSCRIBERS_FILE.read_text())
            subs = data.get("subscribers", [])
            if subs:
                return subs
        except Exception:
            pass
                       
    save_subscribers(DEFAULT_SUBSCRIBERS)
    return DEFAULT_SUBSCRIBERS

def save_subscribers(subscribers):
    SUBSCRIBERS_FILE.write_text(json.dumps({"subscribers": subscribers}, indent=2))

def add_subscriber(chat_id, username=None):
    subs = load_subscribers()
    for s in subs:
        if s["chat_id"] == chat_id:
            return False                      
    subs.append({"chat_id": int(chat_id), "username": username or "", "added_at": int(time.time())})
    save_subscribers(subs)
    return True

def remove_subscriber(chat_id):
    subs = load_subscribers()
    before = len(subs)
    subs = [s for s in subs if s["chat_id"] != int(chat_id)]
    if len(subs) < before:
        save_subscribers(subs)
        return True
    return False

def send_to(chat_id, text, silent=False):
    try:
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
            data=json.dumps({
                "chat_id": chat_id,
                "text": text,
                "disable_notification": silent,
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print("[tg-notify] send to " + str(chat_id) + " failed: " + str(e), flush=True)
        return False

def broadcast(text, silent=False):
                                                                     
    subs = load_subscribers()
    ok = 0
    for s in subs:
        if send_to(s["chat_id"], text, silent=silent):
            ok += 1
    return ok

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for s in load_subscribers():
            print(s)
    elif len(sys.argv) > 2 and sys.argv[1] == "add":
        if add_subscriber(sys.argv[2]):
            print("added")
        else:
            print("already subscribed")
    elif len(sys.argv) > 2 and sys.argv[1] == "remove":
        if remove_subscriber(sys.argv[2]):
            print("removed")
        else:
            print("not found")
    elif len(sys.argv) > 2 and sys.argv[1] == "test":
        n = broadcast(" ".join(sys.argv[2:]))
        print("sent to " + str(n) + " subscribers")
    else:
        print("Usage: tg_notifier.py [list | add CHAT_ID | remove CHAT_ID | test MESSAGE]")
