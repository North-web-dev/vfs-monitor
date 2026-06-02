

import json
import os
import sys
import time
import random
import threading
from pathlib import Path
from curl_cffi import requests

ROOT = Path("/opt/vfs-monitor")
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

def LOG(*a):
    print("[" + time.strftime("%H:%M:%S") + "]", *a, flush=True)

import sys as _sys
_sys.path.insert(0, "/opt/vfs-monitor")
from tg_notifier import broadcast as tg_broadcast
from proxy_pool import random_proxy

CATS = [
    ("LSHMEDCL", "Work UZB/TKM"),
    ("LNGWORK", "Cargo drivers UZB/TKM"),
    ("LNGRSDTJK", "Work TJK"),
    ("LNGWORKTJK", "Cargo drivers TJK"),
]
TJK_CATS = {"LNGRSDTJK", "LNGWORKTJK"}

MISSION = "lva"
COUNTRY = "uzb"
CULTURE = "en-us"

                 
CYCLE_INTERVAL = 30                                                     
PER_REQ_TIMEOUT = 20                                                            
DIRECT_TIMEOUT = 6
ALERT_COOLDOWN = 180
VERIFY_HITS = 2
MAX_RETRY_PER_REQ = 3                                                                         
JITTER_PCT = 0.15                                
COOLDOWN_AFTER_403_STREAK = 5                                          
COOLDOWN_DURATION_SEC = 300                       

                                                              
NODE_INDEX = int(os.environ.get("NODE_INDEX", "0"))
NUM_NODES = int(os.environ.get("NUM_NODES", "1"))
NODE_NAME = os.environ.get("NODE_NAME", "node" + str(NODE_INDEX))

HEADERS = {
    "accept": "application/json",
    "origin": "https://visa.vfsglobal.com",
    "referer": "https://visa.vfsglobal.com/",
    "route": "uzb/en/lva",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
}

def load_state():
    p = DATA / "hunter_state.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {
        "last_state": {}, "last_alert": {}, "cycles": 0,
        "boot_at": time.time(), "hit_streak": {},
        "block_streak": 0, "in_cooldown_until": 0,
    }

def save_state(s):
    (DATA / "hunter_state.json").write_text(json.dumps(s, indent=2))

def tg(text):
    try:
        n = tg_broadcast(text)
        LOG("TG sent to " + str(n) + " subs: " + text[:80])
    except Exception as e:
        LOG("TG fail: " + str(e))

def classify_response(txt, sc):
    if sc == 403:
        return "block-403-204" if "403204" in txt else "block-403"
    if sc == 429:
        return "rate-429"
    if sc == 500:
        return "server-500"
    if sc != 200:
        return "http-" + str(sc)
    if not txt:
        return "empty-body"
    if '"centerName":null' in txt or txt.strip() in ("[]", "[{}]", "{}"):
        return "no-slots"
    try:
        arr = json.loads(txt)
        if isinstance(arr, list):
            for item in arr:
                if isinstance(item, dict):
                    cname = item.get("centerName")
                    err = item.get("error")
                    if cname and (err is None or (isinstance(err, dict) and not err.get("code"))):
                        return "SLOT"
        return "unknown-200"
    except Exception:
        return "parse-err"

def _hit_once(url, use_proxy, timeout):
                                                                 
    kw = dict(headers=HEADERS, timeout=timeout, impersonate="chrome120")
    if use_proxy:
        proxy = random_proxy()
        if not proxy:
            return "no-proxy", "", 0
        kw["proxies"] = {"http": proxy, "https": proxy}
    try:
        r = requests.get(url, **kw)
        txt = r.text or ""
        return classify_response(txt, r.status_code), txt[:1000], r.status_code
    except Exception as e:
        return "timeout-err", str(e)[:120], 0

def hit_cat(cat):

    url = "https://lift-api.vfsglobal.com/master/centerwithslots/{}/{}/{}/{}".format(MISSION, COUNTRY, cat, CULTURE)
    _GOOD = ("no-slots", "SLOT")
    for attempt in range(MAX_RETRY_PER_REQ + 1):
        res = {}
        def _run(ch, up, to):
            res[ch] = _hit_once(url, up, to)
        th = [
            threading.Thread(target=_run, args=("direct", False, DIRECT_TIMEOUT)),
            threading.Thread(target=_run, args=("proxy", True, PER_REQ_TIMEOUT)),
        ]
        for t in th:
            t.start()
        for t in th:
            t.join()
        chans = [res.get("direct"), res.get("proxy")]
                                                   
        for c in chans:
            if c and c[0] == "SLOT":
                return c
                                                 
        for c in chans:
            if c and c[0] == "no-slots":
                return c
                                        
        if attempt < MAX_RETRY_PER_REQ:
            time.sleep(0.8)
            continue
        return chans[0] or chans[1] or ("exhausted", "", 0)
    return "exhausted", "", 0

def verify_slot(cat):
    results = []
    for _ in range(VERIFY_HITS):
        s, body, sc = hit_cat(cat)
        results.append((s, body))
        time.sleep(1.5)
    return results

def trigger_instant_book(cat):
    log = ROOT / "logs" / ("instant_" + cat + "_" + str(int(time.time())) + ".log")
    log.parent.mkdir(exist_ok=True)
    os.system(
        "nohup python3 -u " + str(ROOT) + "/instant_book.py --cat " + cat +
        " >> " + str(log) + " 2>&1 &"
    )
    return "instant_book spawned: " + str(log)

def trigger_prebook(cat):
    if cat not in TJK_CATS:
        return None
    applicant = DATA / "applicant.json"
    if not applicant.exists():
        return None
    log = ROOT / "logs" / ("prebook_" + cat + "_" + str(int(time.time())) + ".log")
    log.parent.mkdir(exist_ok=True)
    os.system(
        "nohup python3 -u " + str(ROOT) + "/prebook_trigger.py --cat " + cat +
        " --applicant " + str(applicant) + " >> " + str(log) + " 2>&1 &"
    )
    return "prebook spawned: " + str(log)

def poll_one(cat, name, state):
    status, body, sc = hit_cat(cat)
    last = state["last_state"].get(cat, "init")

                                                                                        
                                                                     
    TRANSIENT = ("timeout-err", "block-403", "block-403-204", "rate-429", "server-500", "empty-body", "exhausted", "no-proxy", "parse-err")
    if status not in TRANSIENT and not status.startswith("http-"):
        state["last_state"][cat] = status
                                      
    state.setdefault("transient_count", {})
    state["transient_count"][status] = state["transient_count"].get(status, 0) + 1

                           
    if status.startswith("block-403"):
        state["block_streak"] = state.get("block_streak", 0) + 1
        if state["block_streak"] >= COOLDOWN_AFTER_403_STREAK:
            cd = COOLDOWN_DURATION_SEC + NODE_INDEX * 37 + random.randint(0, 60)
            state["in_cooldown_until"] = time.time() + cd
            LOG("CF block streak " + str(state["block_streak"]) + " — entering " + str(cd) + "s cooldown (desync)")
            state["block_streak"] = 0
            if not state.get("cf_alerted"):
                state["cf_alerted"] = True
                tg("🟡 hunter [" + NODE_NAME + "] — CF flagged proxy, pausing ~5 min (retrying quietly)")
    else:
        state["block_streak"] = 0
        if state.get("cf_alerted") and status in ("no-slots", "SLOT"):
            state["cf_alerted"] = False
            tg("🟢 hunter [" + NODE_NAME + "] — proxy recovered, polling resumed")

    if status == "SLOT":
        streak = state["hit_streak"].get(cat, 0) + 1
        state["hit_streak"][cat] = streak
        LOG("[" + cat + "] " + name + ": HIT streak=" + str(streak) + " body=" + body[:200])
        verify = verify_slot(cat)
        verify_pass = sum(1 for s, _ in verify if s == "SLOT") >= VERIFY_HITS - 1
        if verify_pass:
            now = time.time()
            last_alert = state["last_alert"].get(cat, 0)
            if now - last_alert > ALERT_COOLDOWN:
                state["last_alert"][cat] = now
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                msg = (
                    "🎯 SLOT OPEN 🎯\n"
                    + name + " (" + cat + ")\n"
                    + "VAC: TAS, mission: " + MISSION.upper() + "\n"
                    + "detected: " + ts + "\n"
                    + "streak: " + str(streak) + ", verified: " + str(sum(1 for s, _ in verify if s == "SLOT")) + "/" + str(VERIFY_HITS) + "\n"
                    + "body: " + body[:300]
                )
                tg(msg)
                ib = trigger_instant_book(cat)
                LOG("[" + cat + "] " + ib)
                pb = trigger_prebook(cat)
                if pb:
                    LOG("[" + cat + "] " + pb)
    else:
        if state["hit_streak"].get(cat, 0) > 0:
            LOG("[" + cat + "] back to " + status + " - streak reset")
        state["hit_streak"][cat] = 0
        if last != status:
            LOG("[" + cat + "] " + name + ": " + last + " -> " + status)

def main():
    state = load_state()
    save_state(state)                                              
    LOG("=== hunter v2 start (anti-ban) ===")
    LOG("  cycle interval " + str(CYCLE_INTERVAL) + "s, " + str(len(CATS)) + " cats sequential")
    LOG("  ~" + str(round(60 * len(CATS) / CYCLE_INTERVAL, 1)) + " req/min, ~3MB/day estimated traffic")
    tg("🟢 hunter [" + NODE_NAME + "] online — staggered " + str(NODE_INDEX) + "/" + str(NUM_NODES) + ", 30s/cycle")
    if NUM_NODES > 1:
        offset = (CYCLE_INTERVAL / NUM_NODES) * NODE_INDEX
        if offset > 0:
            LOG("stagger phase offset " + str(round(offset, 1)) + "s (node " + str(NODE_INDEX) + "/" + str(NUM_NODES) + ")")
            time.sleep(offset)
    cat_idx = 0
    try:
        while True:
            now = time.time()
                             
            if state.get("in_cooldown_until", 0) > now:
                remaining = state["in_cooldown_until"] - now
                LOG("in cooldown — " + str(int(remaining)) + "s remaining")
                time.sleep(min(remaining, 60))
                continue

                                    
            cat, name = CATS[cat_idx % len(CATS)]
            poll_one(cat, name, state)
            cat_idx += 1

                                                            
            save_state(state)
            if cat_idx % 20 == 0:
                summary = ", ".join(k + ":" + v for k, v in state["last_state"].items())
                LOG("[" + str(cat_idx) + " polls] " + summary)

                                                                      
            base_sleep = CYCLE_INTERVAL / len(CATS)
            jitter = base_sleep * JITTER_PCT
            sleep_for = base_sleep + random.uniform(-jitter, jitter)
            time.sleep(max(1.0, sleep_for))
    except KeyboardInterrupt:
        LOG("interrupted; saving state")
        save_state(state)
    except Exception as e:
        LOG("fatal: " + str(e))
        tg("🔴 slot_hunter v2 crashed: " + str(e))
        save_state(state)
        raise

def _selftest():
    neg = '[{"id":0,"masterId":0,"centerName":null,"missionCode":null,"missionName":null,"countryCode":null,"countryName":null,"cultureCode":null,"isoCode":null,"city":null,"contactNumber":null,"callCenterNumber":null,"address":null,"state":null,"country":null,"pincode":null,"email":null,"website":null,"timeZone":null,"vacType":null,"operationHours":null,"upvFees":0,"upvCurrency":null,"Visacategorycode":null,"isSpecialUser":false,"error":{"code":4100,"description":"Internal Server Error","type":"Information"}}]'
    assert classify_response(neg, 200) == "no-slots"
    pos = '[{"id":1234,"masterId":0,"centerName":"Chennai - Visa Application Centre","missionCode":"deu","countryCode":"ind","isoCode":"MAA","city":"Chennai","error":null}]'
    assert classify_response(pos, 200) == "SLOT"
    assert classify_response("[]", 200) == "no-slots"
    assert classify_response('{"code":"403204"}', 403).startswith("block-403")
    pos2 = '[{"id":6218,"centerName":"VFS GLOBAL SERVICES UBKN","missionCode":"lva","isoCode":"TAS","error":null}]'
    assert classify_response(pos2, 200) == "SLOT"
    print("[selftest] PASS - 5/5")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selftest()
    else:
        main()
