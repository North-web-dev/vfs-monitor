import os

from __future__ import annotations
import argparse, asyncio, base64, json, os, random, sys, time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from curl_cffi.requests import AsyncSession
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
import yaml

ROOT = Path("/opt/vfs-monitor")
DATA = ROOT / "data"
LOGS = ROOT / "logs"
DATA.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = ROOT / "config"

LIFT_API = "https://lift-api.vfsglobal.com"
LOGIN_PAGE = "https://visa.vfsglobal.com/uzb/en/lva/login"
TURNSTILE_SITEKEY = "0x4AAAAAABhlz7Ei4byodYjs"

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT = "int(os.getenv("OWNER_CHAT_ID", "0"))"

CATEGORIES = {
    "LSHMEDCL":   "Work UZB/TKM",
    "LNGWORK":    "Cargo drivers UZB/TKM",
    "LNGRSDTJK":  "Work TJK",
    "LNGWORKTJK": "Cargo drivers TJK",
}

def load_env() -> dict[str, str]:
    env = {}
    p = CONFIG_DIR / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env

def load_rsa_pubkey() -> bytes:
                                                   
    p = CONFIG_DIR / "csk_pubkey.pem"
    if not p.exists():
                                                                       
        legacy = Path("/root/atlix-audit/findings/rsa_pubkey.pem")
        if legacy.exists():
            p.write_bytes(legacy.read_bytes())
        else:
            raise FileNotFoundError(f"no pubkey at {p}")
    return p.read_bytes()

def rsa_encrypt(pem_bytes: bytes, plaintext: str) -> str:
    pub = load_pem_public_key(pem_bytes)
    ct = pub.encrypt(
        plaintext.encode(),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode()

def fresh_clientsource(pem_bytes: bytes) -> str:
                                                                                                 
    return rsa_encrypt(pem_bytes, str(int(time.time() * 1000)))

def random_proxy() -> tuple[str, str]:
                                                                
    proxies = (CONFIG_DIR / "proxies.txt").read_text().strip().splitlines()
    line = random.choice(proxies)
    host, port, user, pw = line.split(":", 3)
    proxy_url = f"http://{user}:{pw}@{host}:{port}"
                             
    import re
    m = re.search(r"sid-([a-f0-9]+)-", user)
    sid = m.group(1) if m else "?"
    return proxy_url, sid

async def tg_send(text: str) -> None:
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=10),
            )
    except Exception as e:
        print(f"  tg_send fail: {e}", flush=True)

async def capsolver_create(api_key: str, task: dict) -> str:
    async with aiohttp.ClientSession() as s:
        r = await s.post("https://api.capsolver.com/createTask",
                         json={"clientKey": api_key, "task": task},
                         timeout=aiohttp.ClientTimeout(total=30))
        data = await r.json()
        if data.get("errorId"):
            raise RuntimeError(f"capsolver create error: {data}")
        return data["taskId"]

async def capsolver_poll(api_key: str, task_id: str, timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    async with aiohttp.ClientSession() as s:
        while time.time() < deadline:
            await asyncio.sleep(3)
            r = await s.post("https://api.capsolver.com/getTaskResult",
                             json={"clientKey": api_key, "taskId": task_id},
                             timeout=aiohttp.ClientTimeout(total=30))
            data = await r.json()
            if data.get("status") == "ready":
                return data["solution"]
            if data.get("errorId"):
                raise RuntimeError(f"capsolver poll err: {data}")
        raise TimeoutError(f"capsolver task {task_id} timed out")

async def solve_turnstile(api_key: str, page_url: str, site_key: str, proxy: str | None = None) -> str:
    task_id = await capsolver_create(api_key, {
        "type": "AntiTurnstileTask" if proxy else "AntiTurnstileTaskProxyLess",
        "websiteURL": page_url, "websiteKey": site_key,
        **({"proxy": proxy, "proxyType": "http"} if proxy else {}),
    })
    sol = await capsolver_poll(api_key, task_id)
    return sol["token"]

async def solve_cf(api_key: str, page_url: str, proxy: str) -> tuple[dict[str, str], str]:
                                             
                             
    task_id = await capsolver_create(api_key, {
        "type": "AntiCloudflareTask",
        "websiteURL": page_url, "proxy": proxy, "proxyType": "http",
    })
    sol = await capsolver_poll(api_key, task_id, timeout=240)
                                                                                 
    cookies = {}
    raw = sol.get("cookies") or []
    if isinstance(raw, list):
        for c in raw:
            if isinstance(c, dict) and c.get("name"):
                cookies[c["name"]] = c["value"]
    elif isinstance(raw, dict):
        cookies.update(raw)
    ua = sol.get("userAgent") or sol.get("user_agent") or ""
    return cookies, ua

async def vfs_login(email: str, password: str, proxy: str, pem: bytes, capsolver_key: str,
                    route: str = "uzb/en/lva", country: str = "uzb", mission: str = "lva") -> dict:
                                                                              
    print(f"[login {email}] solving CF challenge…", flush=True)
    cf_cookies, ua = await solve_cf(capsolver_key, LOGIN_PAGE, proxy)
    if not ua:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    print(f"[login {email}] CF ok, ua={ua[:50]}, cookies={list(cf_cookies)}", flush=True)

    print(f"[login {email}] solving Turnstile…", flush=True)
    turnstile = await solve_turnstile(capsolver_key, LOGIN_PAGE, TURNSTILE_SITEKEY, proxy=proxy)
    print(f"[login {email}] Turnstile token len={len(turnstile)}", flush=True)

    enc_pw = rsa_encrypt(pem, password)
    csource = fresh_clientsource(pem)

    body = urlencode({
        "username": email,
        "password": enc_pw,
        "missioncode": mission,
        "countrycode": country,
        "languageCode": "en-US",
        "captcha_version": "cloudflare-v1",
        "captcha_api_key": turnstile,
    })
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "uz,en-US;q=0.9,en;q=0.8",
        "clientsource": csource,
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://visa.vfsglobal.com",
        "priority": "u=1, i",
        "referer": "https://visa.vfsglobal.com/",
        "route": route,
        "sec-ch-ua": '"Chromium";v="132", "Google Chrome";v="132", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": ua,
    }
                                                                                       
    async with AsyncSession(impersonate="chrome131",
                            proxies={"http": proxy, "https": proxy},
                            verify=False) as s:
                                                                                
        warm_headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "uz,en-US;q=0.9,en;q=0.8",
            "clientsource": csource,
            "origin": "https://visa.vfsglobal.com",
            "referer": "https://visa.vfsglobal.com/",
            "route": route, "user-agent": ua,
            "sec-ch-ua": '"Chromium";v="132", "Google Chrome";v="132", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-site",
        }
        print(f"[login {email}] WARMUP GET /configuration/fields/{mission}/{country}…", flush=True)
        warm_r = await s.get(f"{LIFT_API}/configuration/fields/{mission}/{country}",
                             headers=warm_headers, cookies=cf_cookies, timeout=30)
        print(f"[login {email}] warmup status={warm_r.status_code} cookies_after={list(dict(warm_r.cookies).keys())}", flush=True)
        try:
            cf_cookies = dict(cf_cookies)
            cf_cookies.update(dict(warm_r.cookies))
        except Exception as e:
            print(f"  warm cookie merge err: {e}", flush=True)

                                        
        csource = fresh_clientsource(pem)
        headers["clientsource"] = csource

        print(f"[login {email}] POST /user/login…", flush=True)
        r = await s.post(f"{LIFT_API}/user/login", headers=headers, data=body,
                         cookies=cf_cookies, timeout=60)
        print(f"[login {email}] status={r.status_code}", flush=True)
        print(f"[login {email}] body[:2500]={r.text[:2500]}", flush=True)
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "body": r.text}
        try:
            data = r.json()
        except Exception:
            data = {}
                              
        jwt = (data.get("tokenInfo") or {}).get("token") if isinstance(data.get("tokenInfo"), dict) else None
        if not jwt:
            jwt = data.get("token") or data.get("jwt")
                                             
        all_cookies = dict(cf_cookies)
        try: all_cookies.update(dict(r.cookies))
        except Exception: pass
        return {
            "ok": bool(jwt), "jwt": jwt, "cookies": all_cookies, "user_agent": ua,
            "raw": data,
        }

async def vfs_calendar(session_state: dict, payload: dict, pem: bytes, proxy: str) -> tuple[int, str]:
    csource = fresh_clientsource(pem)
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "uz,en-US;q=0.9,en;q=0.8",
        "authorize": session_state["jwt"],
        "clientsource": csource,
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://visa.vfsglobal.com",
        "priority": "u=1, i",
        "referer": "https://visa.vfsglobal.com/",
        "route": session_state.get("route", "uzb/en/lva"),
        "sec-ch-ua": '"Chromium";v="132", "Google Chrome";v="132", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": session_state["user_agent"],
    }
    async with AsyncSession(impersonate="chrome131") as s:
        r = await s.post(f"{LIFT_API}/appointment/calendar",
                         headers=headers, json=payload,
                         cookies=session_state["cookies"],
                         proxies={"http": proxy, "https": proxy},
                         timeout=60, verify=False)
        return r.status_code, r.text

def extract_dates(body_text: str) -> list[str]:
    try:
        d = json.loads(body_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(d, dict):
        return []
    cals = d.get("calendars") or []
    return sorted({c["date"] for c in cals if isinstance(c, dict) and c.get("date")})

async def monitor(account_id: str, urn: str | None, interval: int) -> None:
    env = load_env()
    capsolver_key = env.get("CAPSOLVER_KEY") or os.environ.get("CAPSOLVER_KEY")
    if not capsolver_key:
        print("FATAL: CAPSOLVER_KEY missing in config/.env", flush=True); sys.exit(1)

                  
    accounts = yaml.safe_load((CONFIG_DIR / "accounts.yaml").read_text())["accounts"]
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        print(f"FATAL: account {account_id} not in accounts.yaml", flush=True); sys.exit(1)

    pem = load_rsa_pubkey()
    proxy, sid = random_proxy()
    print(f"[+] account={account_id} email={acc['email']} sid={sid}", flush=True)

                
    session = await vfs_login(acc["email"], acc["password"], proxy, pem, capsolver_key)
    if not session["ok"]:
        await tg_send(f"❌ direct_api login fail [{account_id}] status={session.get('status')}\n{session.get('body','')[:400]}")
        return
    await tg_send(f"🟢 direct_api logged in [{account_id}] sid={sid}\nuser-agent: {session['user_agent'][:60]}\nmonitoring: {', '.join(CATEGORIES)}")

                                                                                                           
    if not urn:
                                      
        try:
            csource = fresh_clientsource(pem)
            async with AsyncSession(impersonate="chrome131") as s:
                r = await s.post(f"{LIFT_API}/appointment/applicants",
                                 headers={
                                     "authorize": session["jwt"], "clientsource": csource,
                                     "content-type": "application/json;charset=UTF-8",
                                     "route": "uzb/en/lva", "origin": "https://visa.vfsglobal.com",
                                     "referer": "https://visa.vfsglobal.com/", "user-agent": session["user_agent"],
                                 },
                                 json={
                                     "missionCode": "lva", "countryCode": "uzb", "centerCode": "TAS",
                                     "loginUser": acc["email"], "languageCode": "en-US",
                                 },
                                 cookies=session["cookies"],
                                 proxies={"http": proxy, "https": proxy},
                                 timeout=60, verify=False)
                print(f"  /appointment/applicants → {r.status_code}: {r.text[:400]}", flush=True)
                try:
                    ad = r.json()
                    al = ad.get("applicantList") or ad.get("applicants") or []
                    if al:
                        urn = al[0].get("urn")
                except Exception:
                    pass
        except Exception as e:
            print(f"  applicants fetch err: {e}", flush=True)

    if not urn:
        await tg_send(f"⚠️ [{account_id}] no URN — applicant record not bootstrapped. Pass --urn or set up via UI first.")
        return

    print(f"[+] urn={urn}", flush=True)
    await tg_send(f"📋 [{account_id}] urn={urn}, polling every {interval}s ± jitter")

    state_path = DATA / f"direct_state_{account_id}.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    iters = 0
    consec_err = 0
    while True:
        iters += 1
        t0 = time.time()
        try:
            from_date = time.strftime("%d/%m/%Y", time.gmtime())
            results = {}
                                                                                         
            cat_list = list(CATEGORIES)
            code = cat_list[(iters - 1) % len(cat_list)]
            name = CATEGORIES[code]

            payload = {
                "countryCode": "uzb", "missionCode": "lva", "centerCode": "TAS",
                "loginUser": acc["email"], "visaCategoryCode": code,
                "fromDate": from_date, "urn": urn, "payCode": "",
            }
            status, body = await vfs_calendar(session, payload, pem, proxy)
            ts = time.strftime("%H:%M:%S")

            if status == 403 or "Just a moment" in body[:200]:
                consec_err += 1
                msg = f"🛡️ [{account_id}] CF challenge iter#{iters} ({code}) — relogin needed"
                print(f"[{ts}] {msg}", flush=True)
                if consec_err == 1:
                    await tg_send(msg)
                                      
                proxy, sid = random_proxy()
                print(f"  rotating to sid={sid}", flush=True)
                session = await vfs_login(acc["email"], acc["password"], proxy, pem, capsolver_key)
                if not session["ok"]:
                    await tg_send(f"❌ [{account_id}] relogin fail: {session.get('body','')[:200]}")
                    await asyncio.sleep(300)
                continue

            if status == 401:
                consec_err += 1
                msg = f"🔑 [{account_id}] HTTP 401 iter#{iters} ({code}) — JWT expired, relogin"
                print(f"[{ts}] {msg}", flush=True)
                if consec_err == 1:
                    await tg_send(msg)
                proxy, sid = random_proxy()
                session = await vfs_login(acc["email"], acc["password"], proxy, pem, capsolver_key)
                continue

            if status != 200:
                print(f"[{ts}] iter#{iters} {code} → HTTP {status} body[:200]={body[:200]}", flush=True)
                await asyncio.sleep(max(60.0, interval - (time.time() - t0) + random.uniform(-30, 30)))
                continue

            consec_err = 0
            dates = extract_dates(body)
            prev = set(state.get(code, []))
            new_dates = [d for d in dates if d not in prev]
            state[code] = dates
            state["_ts"] = time.time(); state["_iter"] = iters
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))

            if new_dates:
                msg = (f"🎯 <b>NEW SLOTS [{account_id}]</b>\n"
                       f"Category: <b>{name}</b> ({code})\n"
                       f"New dates: {', '.join(new_dates)}\n"
                       f"All available now: {len(dates)}")
                print(f"[{ts}] iter#{iters} NEW {code}={new_dates}", flush=True)
                await tg_send(msg)
            else:
                print(f"[{ts}] iter#{iters} {code} → ok({len(dates)})", flush=True)
        except Exception as e:
            consec_err += 1
            print(f"[{time.strftime('%H:%M:%S')}] iter#{iters} ERR: {type(e).__name__}: {e}", flush=True)
            if consec_err <= 3:
                await tg_send(f"⚠️ [{account_id}] iter#{iters}: {type(e).__name__}: {str(e)[:300]}")
            await asyncio.sleep(60)
            continue

        sleep_for = max(60.0, interval - (time.time() - t0) + random.uniform(-30, 30))
        await asyncio.sleep(sleep_for)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="acc1/acc2/acc3 from accounts.yaml")
    ap.add_argument("--urn", default=None, help="URN to use; auto-fetched from /applicants if omitted")
    ap.add_argument("--interval", type=int, default=600, help="seconds between polls (one category per poll)")
    args = ap.parse_args()
    asyncio.run(monitor(args.account, args.urn, args.interval))

if __name__ == "__main__":
    main()
