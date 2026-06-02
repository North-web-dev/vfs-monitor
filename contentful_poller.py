import os

import json
import time
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path("/opt/vfs-monitor")
DATA = ROOT / "data"
STATE = DATA / "contentful_state.json"

CF_SPACE = "xxg4p8gt3sg6"
CF_TOKEN = os.getenv("CF_TOKEN", "")
CF_BASE = "https://cdn.contentful.com/spaces/" + CF_SPACE + "/environments/master/entries"

import sys as _sys
_sys.path.insert(0, "/opt/vfs-monitor")
from tg_notifier import broadcast as tg_broadcast

                                                                                                   
REQUEST_PACING_SEC = 0.5

                                                          
QUERIES = [
    {"mission": "lva", "country": "uzb"},
    {"mission": "lva", "country": "tjk"},
    {"mission": "lva", "country": "tkm"},
]

CONTENT_TYPES = ["countryNews", "countryNewsflash", "countryCallToAction", "flashBanner", "heroBanner"]

SLOT_KEYWORDS = [
    "appointment", "slot", "schedul", "available", "booking", "book",
    "reopen", "resum", "open", "new date", "additional",
]

POLL_INTERVAL = 300         

def LOG(*a):
    print("[" + time.strftime("%H:%M:%S") + "]", "cf-poll:", *a, flush=True)

def tg(text):
    try:
        n = tg_broadcast(text)
        LOG("TG sent to " + str(n) + " subs: " + text[:80])
    except Exception as e:
        LOG("TG fail: " + str(e))

def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"seen_ids": [], "boot_at": time.time()}

def save_state(s):
    STATE.write_text(json.dumps(s, indent=2))

def extract_text(node):
                                                                        
    if not isinstance(node, dict):
        return ""
    out = node.get("value", "")
    for c in node.get("content", []):
        out += " " + extract_text(c)
    return out

def query_contentful(content_type, q, retries=3):
                                                                         
    params = {
        "access_token": CF_TOKEN,
        "content_type": content_type,
        "query": q,
        "limit": "50",
        "order": "-sys.updatedAt",
    }
    url = CF_BASE + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                                                    
                time.sleep(2 ** attempt)
                continue
            LOG("query err [" + content_type + " q=" + q + "]: HTTP " + str(e.code))
            return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            LOG("query err [" + content_type + " q=" + q + "]: " + str(e))
            return None
    return None

def check_new(state):
                                                                       
    new_hits = []
    seen = set(state.get("seen_ids", []))

    for corr in QUERIES:
        for ct in CONTENT_TYPES:
                                                    
            for q in [corr["mission"] + " " + corr["country"], corr["mission"] + " > " + corr["country"]]:
                data = query_contentful(ct, q)
                                              
                time.sleep(REQUEST_PACING_SEC)
                if not data:
                    continue
                for item in data.get("items", []):
                    iid = item.get("sys", {}).get("id")
                    if not iid or iid in seen:
                        continue
                    fields = item.get("fields", {})
                    locale = fields.get("locale", "").lower()
                                         
                    if corr["mission"] not in locale or corr["country"] not in locale:
                        continue
                    title = fields.get("title", "")
                    body_text = extract_text(fields.get("body", {})) + " " + extract_text(fields.get("intro", {})) + " " + title
                    body_text_l = body_text.lower()
                                         
                    matched_keywords = [k for k in SLOT_KEYWORDS if k in body_text_l]
                    is_slot_related = bool(matched_keywords)
                    hit = {
                        "id": iid,
                        "content_type": ct,
                        "locale": fields.get("locale", ""),
                        "title": title,
                        "date": fields.get("date", ""),
                        "updated_at": item.get("sys", {}).get("updatedAt", ""),
                        "permanent": fields.get("permanent", False),
                        "matched_keywords": matched_keywords,
                        "body_preview": body_text[:300],
                        "is_slot": is_slot_related,
                    }
                    new_hits.append(hit)
                    seen.add(iid)

    state["seen_ids"] = list(seen)[-1000:]                  
    state["last_check"] = time.time()
    return new_hits

def main():
    state = load_state()
    LOG("=== contentful_poller start, watching " + str(len(QUERIES)) + " corridors x " + str(len(CONTENT_TYPES)) + " types ===")
    tg("📡 Contentful poller online — watching " + str(len(QUERIES)) + " VFS corridors via CMS leak")
    while True:
        try:
            hits = check_new(state)
            slot_hits = [h for h in hits if h["is_slot"]]
            if hits:
                LOG("found " + str(len(hits)) + " new entries (" + str(len(slot_hits)) + " slot-related)")
                for h in hits:
                    marker = "🎯" if h["is_slot"] else "📰"
                    LOG(" " + marker + " " + h["content_type"] + " | " + h["locale"] + " | " + h["title"][:80])
                for h in slot_hits:
                    msg = (
                        "🎯 CONTENTFUL SLOT SIGNAL 🎯\n"
                        + "type: " + h["content_type"] + "\n"
                        + "locale: " + h["locale"] + "\n"
                        + "title: " + h["title"] + "\n"
                        + "date: " + h["date"] + "\n"
                        + "keywords: " + ", ".join(h["matched_keywords"]) + "\n"
                        + "preview: " + h["body_preview"]
                    )
                    tg(msg)
            save_state(state)
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            LOG("interrupted")
            save_state(state)
            return
        except Exception as e:
            LOG("loop err: " + str(e))
            time.sleep(POLL_INTERVAL)

def cmd_test():
                                                           
    state = load_state()
    print("=== Test run ===")
    hits = check_new(state)
    if not hits:
        print("No new entries since last check.")
        return
    for h in hits:
        marker = "🎯" if h["is_slot"] else "📰"
        print(f"{marker} [{h['content_type']}] {h['locale']} | {h['title']}")
        print(f"   keywords: {h['matched_keywords']}")
        print(f"   preview: {h['body_preview'][:200]}")
        print()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        cmd_test()
    else:
        main()
