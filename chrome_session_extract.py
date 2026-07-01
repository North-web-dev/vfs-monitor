

import json
import time
import urllib.request
from pathlib import Path
import websocket

ROOT = Path("/opt/vfs-monitor")
DATA = ROOT / "data"
CDP_URL = "http://127.0.0.1:9223"

def LOG(*a):
    print("[" + time.strftime("%H:%M:%S") + "]", "extract:", *a, flush=True)

def cdp_call(ws, _id_ref, method, params=None):
    _id_ref[0] += 1
    ws.send(json.dumps({"id": _id_ref[0], "method": method, "params": params or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == _id_ref[0]:
            return r

def get_chrome_state():
                                                                                               
    try:
        tabs = json.loads(urllib.request.urlopen(CDP_URL + "/json", timeout=5).read())
    except Exception as e:
        return None, "cant connect to CDP: " + str(e)
    page = next((t for t in tabs if t.get("type") == "page" and "visa.vfsglobal.com" in t.get("url", "")), None)
    if not page:
        return None, "no visa.vfsglobal.com tab"
    try:
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=10)
    except Exception as e:
        return None, "ws connect fail: " + str(e)
    _id_ref = [0]
    try:
        cdp_call(ws, _id_ref, "Runtime.enable")
        r = cdp_call(ws, _id_ref, "Runtime.evaluate",
                     {"expression": "JSON.stringify(Object.fromEntries(Object.entries(sessionStorage)))",
                      "returnByValue": True})
        ss_raw = r.get("result", {}).get("result", {}).get("value", "{}")
        sessionStorage = json.loads(ss_raw) if ss_raw else {}
        r = cdp_call(ws, _id_ref, "Network.getCookies",
                     {"urls": ["https://lift-api.vfsglobal.com", "https://visa.vfsglobal.com"]})
        cookies_list = r.get("result", {}).get("cookies", [])
        cookies = {c["name"]: c["value"] for c in cookies_list}
    finally:
        ws.close()
    return {"sessionStorage": sessionStorage, "cookies": cookies, "url": page["url"]}, None

def extract_login_user(ss):
                                                 
                                                                                                     
    candidates = ["loginUser", "userEmail", "user_email", "email", "loggedUser"]
    for k in candidates:
        if k in ss:
            return ss[k]
                                 
    for k, v in ss.items():
        if isinstance(v, str) and "@" in v and ".com" in v:
            return v
    return None

def write_session(email, jwt, cookies, ss, extras=None):
                                                                       
    safe_email = email.replace("@", "_at_")
    p = DATA / ("session_" + safe_email + ".json")
    existing = {}
    if p.exists():
        try: existing = json.loads(p.read_text())
        except: pass
    session = {
        **existing,
        "loginUser": email,
        "jwt": jwt,
        "captured_at": int(time.time()),
        "cookies": cookies,
        "source": "chrome_cdp_extract",
        "missionCode": "lva",
        "countryCode": "uzb",
        "centerCode": "TAS",
        "csk_str_raw": ss.get("csk_str", ""),
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    }
    if extras: session.update(extras)
    p.write_text(json.dumps(session, indent=2, ensure_ascii=False))
    return p

def main():
    state, err = get_chrome_state()
    if not state:
        LOG("FAIL: " + (err or "unknown"))
        return 1
    ss = state["sessionStorage"]
    cookies = state["cookies"]
    url = state["url"]
    LOG("Chrome on URL: " + url)
    LOG("sessionStorage keys: " + str(list(ss.keys())))
    LOG("cookies count: " + str(len(cookies)) + " names: " + str(list(cookies.keys())))

                               
    jwt = None
    jwt_keys = []
    for k in ss:
        if "auth" in k.lower() or "jwt" in k.lower() or "token" in k.lower() or "access" in k.lower():
            jwt_keys.append(k)
            v = ss[k]
            if isinstance(v, str) and v.startswith("EAAAA"):
                jwt = v
                LOG("found JWT in sessionStorage[" + k + "] (" + str(len(v)) + " chars)")
                break

    if not jwt:
        LOG("NO JWT — operator not logged in. JWT-related keys: " + str(jwt_keys))
        LOG("Operator: open the noVNC console and log in any account (host and password from deploy env).")
        return 0                                 

    email = extract_login_user(ss)
    if not email:
        LOG("found JWT but no login email — using \"unknown@chrome.local\"")
        email = "unknown@chrome.local"

    p = write_session(email, jwt, cookies, ss)
    LOG("session written: " + str(p))
    LOG("  email=" + email + " jwt_len=" + str(len(jwt)) + " cookies=" + str(len(cookies)))
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
