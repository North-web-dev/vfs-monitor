

import os, sys, json, time, subprocess
from urllib.parse import urlparse
sys.path.insert(0, "/opt/vfs-monitor")
import yaml
import direct_api as da
from tg_notifier import broadcast as tg_broadcast
from curl_cffi import requests

ROOT = "/opt/vfs-monitor"; DATA = ROOT + "/data"
ACCOUNT = os.environ.get("VFS_ACCOUNT", "acc1")
NODE = os.environ.get("NODE_NAME", "node")
LIFT = "https://lift-api.vfsglobal.com"
CATS = [("LNGSTUD", "Студ"), ("LSHMEDCL", "Work UZB"), ("LNGWORK", "Cargo UZB"),
        ("LNGRSDTJK", "Work TJK"), ("LNGWORKTJK", "Cargo TJK")]
PACING = 25; JWT_MAX_AGE = 90 * 60; RELOGIN_THROTTLE = 600; ALERT_COOLDOWN = 300
STATE_FILE = DATA + "/authed_state.json"

env = da.load_env(); CAP = env.get("CAPSOLVER_KEY") or os.environ.get("CAPSOLVER_KEY") or ""
PEM = da.load_rsa_pubkey()
ACC = next(a for a in yaml.safe_load(open(ROOT + "/config/accounts.yaml"))["accounts"] if a["id"] == ACCOUNT)
EMAIL = ACC["email"]
SESSION_FILE = DATA + "/session_" + EMAIL.replace("@", "_at_") + ".json"

ST = {"node": NODE, "account": ACCOUNT, "sid": None, "jwt_age_min": None, "cf_ok": False,
      "last_poll": 0, "boot_at": time.time(), "cats": {}, "slots": {}, "cycles": 0,
      "errors": {}, "last_login": 0, "cf_clearance": None}

def LOG(*a):
    print("[" + time.strftime("%H:%M:%S") + "]", NODE, ACCOUNT, *a, flush=True)

def load_st():
    global ST
    try:
        d = json.load(open(STATE_FILE)); d.update({"node": NODE, "account": ACCOUNT}); ST.update(d)
    except Exception:
        pass

def save_st():
    ST["updated"] = time.time(); json.dump(ST, open(STATE_FILE, "w"), indent=2, ensure_ascii=False)

def tg(t):
    try:
        tg_broadcast(t)
    except Exception as e:
        LOG("tg fail", str(e)[:50])

def relogin():
    if time.time() - ST.get("last_login", 0) < RELOGIN_THROTTLE:
        LOG("relogin throttled"); return
    ST["last_login"] = time.time(); save_st()
    LOG("re-login...")
    try:
        subprocess.run(["python3", ROOT + "/auto_login.py", "--account", ACCOUNT], timeout=150, capture_output=True)
    except Exception as e:
        LOG("relogin err", str(e)[:50])

def load_session():
    d = json.load(open(SESSION_FILE)); proxy = d.get("proxy")
    if not proxy and d.get("proxy_sid"):
        for ln in open(ROOT + "/config/proxies.txt"):
            if d["proxy_sid"] in ln:
                h, p, u, pw = ln.strip().split(":", 3); proxy = "http://%s:%s@%s:%s" % (u, pw, h, p); break
    return d, proxy

def cap_cf(cap_proxy, ua, html403):
    task = {"type": "AntiCloudflareTask", "websiteURL": LIFT + "/appointment/CheckIsSlotAvailable", "proxy": cap_proxy, "userAgent": ua}
    if html403:
        task["html"] = html403
    for _ in range(3):                                                      
        try:
            r = requests.post("https://api.capsolver.com/createTask", json={"clientKey": CAP, "task": task}, timeout=30).json()
        except Exception:
            time.sleep(2); continue
        tid = r.get("taskId")
        if not tid:
            time.sleep(2); continue
        end = time.time() + 180
        while time.time() < end:
            time.sleep(3)
            try:
                rr = requests.post("https://api.capsolver.com/getTaskResult", json={"clientKey": CAP, "taskId": tid}, timeout=30).json()
            except Exception:
                continue
            if rr.get("status") == "ready":
                cf = (rr.get("solution", {}).get("cookies") or {}).get("cf_clearance")
                if cf:
                    return cf
                break
            if rr.get("errorId"):
                break
        time.sleep(2)
    return None

def H(jwt, ua):
    return {"accept": "application/json, text/plain, */*", "accept-language": "uz,en-US;q=0.9,en;q=0.8",
            "authorize": jwt, "clientsource": da.fresh_clientsource(PEM), "content-type": "application/json;charset=UTF-8",
            "origin": "https://visa.vfsglobal.com", "priority": "u=1, i", "referer": "https://visa.vfsglobal.com/",
            "route": "uzb/en/lva", "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"', "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors", "sec-fetch-site": "same-site", "user-agent": ua}

def B(cat):
    return {"countryCode": "uzb", "missionCode": "lva", "vacCode": "TAS", "visaCategoryCode": cat,
            "roleName": "Individual", "loginUser": EMAIL, "payCode": ""}

class Ctx:
    jwt = None; ua = None; proxy = None; cap_proxy = None; sess = None; last_cf = 0; cf_backoff = 300

def setup():
    if (not os.path.exists(SESSION_FILE)) or (time.time() - json.load(open(SESSION_FILE)).get("captured_at", 0) > JWT_MAX_AGE):
        relogin()
    d, Ctx.proxy = load_session()
    Ctx.jwt = d["jwt"]
    Ctx.ua = d.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    u = urlparse(Ctx.proxy); Ctx.cap_proxy = "%s:%s:%s:%s" % (u.hostname, u.port, u.username, u.password)
    Ctx.sess = requests.Session(); Ctx.sess.proxies = {"http": Ctx.proxy, "https": Ctx.proxy}
    for k, v in (d.get("cookies") or {}).items():
        Ctx.sess.cookies.set(k, v, domain=".vfsglobal.com")
    if ST.get("cf_clearance"):
        Ctx.sess.cookies.set("cf_clearance", ST["cf_clearance"], domain=".vfsglobal.com")
    ST["sid"] = d.get("proxy_sid")
    ST["jwt_age_min"] = round((time.time() - d.get("captured_at", time.time())) / 60, 1)
    LOG("session ready sid=%s" % d.get("proxy_sid")); save_st()

def ensure_cf(probe_text=None):
    cf = cap_cf(Ctx.cap_proxy, Ctx.ua, probe_text)
    if cf:
        ST["cf_clearance"] = cf; ST["cf_ok"] = True
        Ctx.sess.cookies.set("cf_clearance", cf, domain=".vfsglobal.com"); save_st()
        LOG("cf_clearance refreshed"); return True
    ST["cf_ok"] = False; save_st(); return False

def main():
    load_st()
    ST["errors"] = {}
    LOG("=== authed_hunter start ===")
    tg("🟢 authed [%s] online · acc %s · 5 cats" % (NODE, ACCOUNT))
    setup()
                
    r = Ctx.sess.post(LIFT + "/appointment/CheckIsSlotAvailable", json=B("LNGSTUD"), headers=H(Ctx.jwt, Ctx.ua), timeout=30, impersonate="chrome131")
    if r.status_code == 403:
        ensure_cf(r.text)
    while True:
        for cat, desc in CATS:
            try:
                r = Ctx.sess.post(LIFT + "/appointment/CheckIsSlotAvailable", json=B(cat), headers=H(Ctx.jwt, Ctx.ua), timeout=30, impersonate="chrome131")
                sc = r.status_code; txt = r.text or ""
                ST["last_poll"] = time.time()
                if sc == 403 and "Just a moment" in txt[:120]:
                    ST["cf_ok"] = False
                    if time.time() - Ctx.last_cf > Ctx.cf_backoff:                                       
                        Ctx.last_cf = time.time()
                        if ensure_cf(txt):
                            Ctx.cf_backoff = 600                                                             
                        else:
                            Ctx.cf_backoff = min(Ctx.cf_backoff * 2, 1800)                                    
                    ST["errors"]["cf-403"] = ST["errors"].get("cf-403", 0) + 1
                    ST["cycles"] += 1; save_st(); time.sleep(PACING); continue
                if sc == 401 and "401101" in txt:
                    LOG("401101 -> relogin"); ST["errors"]["401101"] = ST["errors"].get("401101", 0) + 1
                    relogin(); setup(); ensure_cf(); continue
                if sc == 429 or "429201" in txt:
                    ST["errors"]["429"] = ST["errors"].get("429", 0) + 1; save_st(); time.sleep(PACING * 2); continue
                if sc == 200:
                    d = json.loads(txt); earliest = d.get("earliestDate"); err = d.get("error")
                    if earliest and err is None:
                        ST["cats"][cat] = "SLOT:" + str(earliest); ST["slots"][cat] = earliest
                        now = time.time()
                        if now - ST.get("last_alert_" + cat, 0) > ALERT_COOLDOWN:
                            ST["last_alert_" + cat] = now
                            tg("🎯🎯 SLOT — %s (%s)\nдата: %s\nслоты: %s\nнода: %s · acc %s" % (desc, cat, earliest, json.dumps(d.get("earliestSlotLists") or [], ensure_ascii=False)[:180], NODE, ACCOUNT))
                            LOG("SLOT", cat, earliest)
                    else:
                        ST["cats"][cat] = "no-slots"; ST["slots"].pop(cat, None)
                else:
                    ST["errors"]["http-" + str(sc)] = ST["errors"].get("http-" + str(sc), 0) + 1
            except Exception as e:
                ST["errors"]["exc"] = ST["errors"].get("exc", 0) + 1; LOG("err", cat, str(e)[:50])
            ST["cycles"] += 1
            if ST["cycles"] % 5 == 0:
                save_st()
            time.sleep(PACING)

if __name__ == "__main__":
    main()
