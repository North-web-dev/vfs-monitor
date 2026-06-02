

from __future__ import annotations
import argparse, asyncio, base64, json, random, re, time, uuid, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from curl_cffi.requests import AsyncSession
from urllib.parse import urlencode
import aiohttp, yaml

ROOT = Path("/opt/vfs-monitor")
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)

LIFT_API = "https://lift-api.vfsglobal.com"
LOGIN_PAGE = "https://visa.vfsglobal.com/uzb/en/lva/login"
TURNSTILE_SITEKEY = "0x4AAAAAABhlz7Ei4byodYjs"

PEM = (ROOT / "config" / "csk_pubkey.pem").read_bytes()
ENV = {}
for line in (ROOT / "config" / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1)
        ENV[k] = v.strip().strip("'\"")
CAPSOLVER = ENV["CAPSOLVER_KEY"]

def rsa_encrypt(plain: bytes) -> str:
    pub = load_pem_public_key(PEM)
    ct = pub.encrypt(plain, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(), label=None))
    return base64.b64encode(ct).decode()

def cs_now(prefix: str = "GA", tz_hours: int = 5) -> str:
                                                                                   
    tz = timezone(timedelta(hours=tz_hours))
    ts = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S")
    return rsa_encrypt(f"{prefix};{ts}Z".encode())

def fake_dt_cookies() -> dict[str, str]:
    now_ms = int(time.time() * 1000)
    return {
        "dtCookie": f"-20${uuid.uuid4().hex.upper()[:32]}",
        "rxVisitor": f"{now_ms}{uuid.uuid4().hex.upper()[:30]}RC",
        "rxvt": f"{now_ms + 1800000}|{now_ms}",
        "dtPC": f"-20${now_ms}_{uuid.uuid4().hex.upper()[:3]}h1{uuid.uuid4().hex.upper()[:21]}",
        "lt_sn": str(uuid.uuid4()),
    }

def random_proxy() -> tuple[str, str]:
                                                                                                                
    lines = [l for l in (ROOT / "config" / "proxies.txt").read_text().splitlines() if l.strip()]
    h, p, u, pw = random.choice(lines).split(":", 3)
    m = re.search(r"sid-([a-f0-9]+)-", u)
    sid = m.group(1) if m else "rot-" + str(random.randint(100000,999999))
    proto = "socks5" if p == "1080" else "http"
    return f"{proto}://{u}:{pw}@{h}:{p}", sid

def random_long_proxy() -> tuple[str, str]:

    long_path = ROOT / "config" / "proxies_long.txt"
    if not long_path.exists():
        return random_proxy()
    lines = [l for l in long_path.read_text().splitlines() if l.strip()]
    if not lines:
        return random_proxy()
    h, p, u, pw = random.choice(lines).split(":", 3)
    m = re.search(r"sid-([a-f0-9]+)-", u)
    sid = m.group(1) if m else "rot-" + str(random.randint(100000,999999))
    return f"socks5://{u}:{pw}@{h}:{p}", sid

async def capsolver_task(task_spec: dict, label: str) -> dict:
    async with aiohttp.ClientSession() as s:
        r = await s.post("https://api.capsolver.com/createTask",
                         json={"clientKey": CAPSOLVER, "task": task_spec})
        d = await r.json()
        if d.get("errorId"):
            raise RuntimeError(f"capsolver {label} create: {d}")
        tid = d["taskId"]
        for _ in range(80):
            await asyncio.sleep(3)
            r = await s.post("https://api.capsolver.com/getTaskResult",
                             json={"clientKey": CAPSOLVER, "taskId": tid})
            d = await r.json()
            if d.get("status") == "ready":
                return d["solution"]
            if d.get("errorId"):
                raise RuntimeError(f"capsolver {label} poll: {d}")
        raise TimeoutError(f"capsolver {label} timed out")

def _capsolver_proxy_kwargs(proxy: str) -> dict:
                                                                         
    from urllib.parse import urlparse
    parsed = urlparse(proxy)
    proto = parsed.scheme
    host = parsed.hostname
    port = parsed.port
    user = parsed.username
    pw = parsed.password
    cap_proxy = f"{host}:{port}:{user}:{pw}"
    cap_type = "socks5" if proto == "socks5" else "http"
    return {"proxy": cap_proxy, "proxyType": cap_type}

import os as _os, json as _json, time as _time
_CFCACHE = "/opt/vfs-monitor/data/cf_cache.json"
def _cf_cache_get(sid, ttl=50000):
    try:
        e = _json.load(open(_CFCACHE)).get(sid)
        if e and _time.time() - e["ts"] < ttl:
            return e
    except Exception:
        pass
    return None
def _cf_cache_put(sid, cf, ua):
    try:
        try: d = _json.load(open(_CFCACHE))
        except Exception: d = {}
        d[sid] = {"cf": cf, "ua": ua, "ts": _time.time()}
        _json.dump(d, open(_CFCACHE, "w"))
    except Exception:
        pass

async def solve_cf(proxy: str) -> tuple[dict, str]:
                                                          
                                                                                                          
                                                                                             
    sol = await capsolver_task({
        "type": "AntiCloudflareTask",
        "websiteURL": "https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable",
        **_capsolver_proxy_kwargs(proxy),
    }, "CF")
    raw = sol.get("cookies") or {}
    cookies = dict(raw) if isinstance(raw, dict) else {c["name"]: c["value"] for c in raw if isinstance(c, dict)}
    return cookies, sol.get("userAgent", "")

async def solve_turnstile(proxy: str) -> str:
    sol = await capsolver_task({
        "type": "AntiTurnstileTask",
        "websiteURL": LOGIN_PAGE,
        "websiteKey": TURNSTILE_SITEKEY,
        **_capsolver_proxy_kwargs(proxy),
    }, "Turnstile")
    return sol["token"]

import hashlib as _hashlib
                                                                                         
                                                                                        
FINGERPRINTS = [
    {"imp": "chrome131",  "plat": '"Windows"',
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
     "sec": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'},
    {"imp": "chrome133a", "plat": '"Windows"',
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
     "sec": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"'},
    {"imp": "chrome136",  "plat": '"Windows"',
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
     "sec": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"'},
    {"imp": "chrome142",  "plat": '"Windows"',
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
     "sec": '"Google Chrome";v="142", "Chromium";v="142", "Not_A Brand";v="99"'},
    {"imp": "chrome124",  "plat": '"macOS"',
     "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
     "sec": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"'},
    {"imp": "chrome145",  "plat": '"Windows"',
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
     "sec": '"Google Chrome";v="145", "Chromium";v="145", "Not_A Brand";v="24"'},
]
def fingerprint(email: str) -> dict:
    h = int(_hashlib.md5(email.encode()).hexdigest(), 16)
    return FINGERPRINTS[h % len(FINGERPRINTS)]

async def auto_login(email: str, password: str, max_retries: int = 3, fixed_proxy: str | None = None) -> dict | None:

    for attempt in range(max_retries):
                                                                                                  
                                                                 
        if fixed_proxy and attempt == 0:                                                  
            proxy = fixed_proxy
            _m = re.search(r"sid-([a-f0-9]+)-", fixed_proxy); sid = _m.group(1) if _m else "fixed"
        else:
            proxy, sid = random_proxy()                                                              
        print(f"[attempt {attempt+1}/{max_retries}] sid={sid}", flush=True)
        fp = fingerprint(email); ua = fp["ua"]; imp = fp["imp"]                                
                                                                                                    
        try:
            async with AsyncSession(impersonate=imp, proxies={"http": proxy, "https": proxy}, verify=False) as _pc:
                _pr = await _pc.get("https://visa.vfsglobal.com/uzb/en/lva/login", timeout=12)
            if _pr.status_code == 403 or "Just a moment" in (_pr.text[:600] or ""):
                print(f"  пре-чек: IP CF-флагнут → ротация БЕЗ капчи (Capsolver цел)", flush=True)
                continue
        except Exception as _pe:
            print(f"  прокси мёртв/503 ({str(_pe)[:50]}) → ротация на свежий IP, ретрай", flush=True)
            continue
        try:
            _sm = re.search(r"sid-([a-f0-9]+)-", proxy); _sk = _sm.group(1) if _sm else proxy[:24]
            _cc = _cf_cache_get(_sk)
            if _cc:
                _cfc, _cfua = _cc["cf"], _cc["ua"]; print("  cf_clearance: CACHED", flush=True)
            else:
                _cfck, _cfua = await solve_cf(proxy)
                _cfc = (_cfck or {}).get("cf_clearance")
                if _cfc: _cf_cache_put(_sk, _cfc, _cfua)
            if _cfc: ua = _cfua
        except Exception:
            _cfc = None
        try:
            turnstile = await solve_turnstile(proxy)
            print("  Turnstile len=" + str(len(turnstile)) + " cf=" + ("OK" if _cfc else "NO"), flush=True)
        except Exception as e:
            print(f"  capsolver TS err: {e}", flush=True)
            continue

        cookies = fake_dt_cookies()
        if _cfc: cookies["cf_clearance"] = _cfc
        headers_base = {
            "accept-language": "uz,en-US;q=0.9,en;q=0.8",
            "origin": "https://visa.vfsglobal.com",
            "priority": "u=1, i",
            "referer": "https://visa.vfsglobal.com/",
            "route": "uzb/en/lva",
            "sec-ch-ua": fp["sec"],
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": fp["plat"],
            "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-site",
            "user-agent": ua,
        }
        async with AsyncSession(impersonate=imp,
                                proxies={"http": proxy, "https": proxy},
                                verify=False) as s:
                                               
            try:
                r1 = await s.get("https://visa.vfsglobal.com/uzb/en/lva/login",
                                 headers={"accept": "text/html", "user-agent": ua,
                                          "accept-language": headers_base["accept-language"]},
                                 cookies=cookies, timeout=15)
                cookies.update(dict(r1.cookies))
                print(f"  warm visa HTML: {r1.status_code} +{list(dict(r1.cookies))}", flush=True)
            except Exception as e:
                print(f"  warm visa err: {e}", flush=True)

                                                            
            try:
                cfg_headers = {**headers_base, "accept": "application/json, text/plain, */*",
                               "clientsource": cs_now()}
                r2 = await s.get(f"{LIFT_API}/configuration/fields/lva/uzb",
                                 headers=cfg_headers, cookies=cookies, timeout=15)
                cookies.update(dict(r2.cookies))
                print(f"  warm lift-cfg: {r2.status_code} +{list(dict(r2.cookies))}", flush=True)
            except Exception as e:
                print(f"  warm lift-cfg err: {e}", flush=True)

                   
            body = urlencode({
                "username": email,
                "password": rsa_encrypt(password.encode()),
                "missioncode": "lva", "countrycode": "uzb", "languageCode": "en-US",
                "captcha_version": "cloudflare-v1", "captcha_api_key": turnstile,
            })
            login_headers = {**headers_base,
                             "accept": "application/json, text/plain, */*",
                             "clientsource": cs_now(),
                             "cfmlift": "mobile",                           
                             "content-type": "application/x-www-form-urlencoded"}
            try:
                r = await s.post(f"{LIFT_API}/user/login", headers=login_headers,
                                 data=body, cookies=cookies, timeout=30)
                status = r.status_code; text = r.text
                                                                                     
                                                                                                   
                cookies.update(dict(r.cookies))
            except Exception as e:
                print(f"  POST err: {e}", flush=True); continue

            print(f"  LOGIN status={status} body[:200]={text[:200]}", flush=True)
            if status == 403 and "Just a moment" in text[:200]:
                                                                                    
                                                                                         
                                                                                     
                print(f"  CF-403 на POST (редко, пре-чек ловит заранее) → бенч БЕЗ повторного solve", flush=True)
                return None
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                print(f"  non-JSON body → бенч (не жжём повторный Turnstile)"); return None

                                                                                           
                                                                                 
            if status == 429 or data.get("code") in ("429001", "429201"):
                print(f"  RATE LIMIT status={status} code={data.get('code')} — abort, backoff", flush=True)
                return None

            err = data.get("error")
            if isinstance(err, dict) and err.get("code"):
                code = err["code"]; desc = err.get("description", "")
                print(f"  AUTH FAIL: code={code} desc={desc}", flush=True)
                return None
            if isinstance(err, str) and err:
                print(f"  AUTH FAIL: error string = {err[:120]}", flush=True)
                return None
            if data.get("isAuthenticated") and data.get("accessToken"):
                                        
                session = {
                    "captured_at": int(time.time()),
                    "loginUser": email,
                    "countryCode": "uzb", "missionCode": "lva", "centerCode": "TAS",
                    "jwt": data["accessToken"],
                    "csk_str_raw": PEM.decode().replace("-----BEGIN PUBLIC KEY-----\n", "").replace("\n-----END PUBLIC KEY-----\n", ""),
                    "cookies": cookies,
                    "proxy": proxy,
                    "proxy_sid": sid,
                    "user_agent": ua,
                    "impersonate": imp,
                    "sec_ch_ua": fp["sec"],
                    "sec_ch_ua_platform": fp["plat"],
                    "roleName": data.get("roleName"),
                    "remainingCount": data.get("remainingCount"),
                    "dialCode": data.get("dialCode"),
                    "contactNumber": data.get("contactNumber"),
                    "raw_login_response": data,
                }
                out = DATA / f"session_{email.replace('@', '_at_')}.json"
                out.write_text(json.dumps(session, indent=2, ensure_ascii=False))
                print(f"  ✅ SAVED → {out}", flush=True)
                return session
            print(f"  weird response → бенч (не жжём повторный Turnstile)", flush=True)
            return None

    print("FAILED all retries", flush=True)
    return None

def get_account(account_id: str) -> tuple[str, str]:
    accs = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
    for a in accs:
        if a["id"] == account_id:
            return a["email"], a["password"]
    raise SystemExit(f"account {account_id} not found in accounts.yaml")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", help="account id from accounts.yaml (e.g. acc3)")
    ap.add_argument("--email", help="if not using --account")
    ap.add_argument("--password", help="if not using --account")
    args = ap.parse_args()
    if args.account:
        email, password = get_account(args.account)
    elif args.email and args.password:
        email, password = args.email, args.password
    else:
        ap.print_help(); sys.exit(1)
    asyncio.run(auto_login(email, password))

if __name__ == "__main__":
    main()
