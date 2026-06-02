import os

from __future__ import annotations
import argparse, asyncio, json, os, random, time, sys
from pathlib import Path
import aiohttp, yaml
from curl_cffi.requests import AsyncSession

sys.path.insert(0, "/opt/vfs-monitor")
from auto_login import auto_login as do_login, cs_now, DATA, ROOT, LIFT_API
from tg_notifier import broadcast                                                

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT = "int(os.getenv("OWNER_CHAT_ID", "0"))"
NODE = os.environ.get("NODE_NAME", "node")
CONTROL_CAT = "LNGSTUD"                                                               
POOL_STATE = DATA / f"pool_state_{NODE}.json"
_ns = {"node": NODE, "ts": 0, "accts": 0, "live": 0, "control_ok": False, "cats": {}}

CAP_SOLVES_LOG = DATA / "cap_solves.log"
def _cap_solves_1h() -> tuple[int, int]:
                                                                                                     
    try:
        lines = CAP_SOLVES_LOG.read_text().split()
        cutoff = time.time() - 3600
        h = sum(1 for x in lines if x.isdigit() and int(x) >= cutoff)
        if len(lines) > 5000:                                      
            CAP_SOLVES_LOG.write_text("\n".join(lines[-2000:]) + "\n")
        return h, len(lines)
    except Exception:
        return 0, 0

def write_node_state():
    try:
        _ns["node"] = NODE; _ns["ts"] = int(time.time())
        _ns["solves_1h"], _ns["solves_total"] = _cap_solves_1h()
        POOL_STATE.write_text(json.dumps(_ns))
    except Exception:
        pass
LOGIN_COOLDOWN = 120                                                                                              
JWT_TTL = 21600                                                                                             
DEAD_COOLDOWN = 1800                                                             
TARGET_LIVE = int(os.environ.get("TARGET_LIVE", "5"))                                             
REST = 60                                                                          
REST_429 = 1800                                                                            
MIN_LOGIN_GAP = 15                                                                                                          
REFILL_SLEEP = 10                                                                                         
POLL_GAP = int(os.environ.get("POLL_GAP", "60"))                                                                                              
LOGIN_FAIL_LIMIT = int(os.environ.get("LOGIN_FAIL_LIMIT", "8"))                                                            
LOGIN_FREEZE = int(os.environ.get("LOGIN_FREEZE", "1800"))                                                       
CF429_ROTATE = int(os.environ.get("CF429_ROTATE", "5"))                                                                                 

def _rotate_sid(proxy: str) -> str:
                                                                                         
    import re, secrets
    return re.sub(r"(-sid-)[A-Za-z0-9]+(-)", lambda m: f"{m.group(1)}{secrets.token_hex(7)}{m.group(2)}", proxy, count=1)

CATEGORIES = {
    "LNGSTUD":    "Студенты UZB",
    "LSHMEDCL":   "Work UZB/TKM",
    "LNGWORK":    "Cargo drivers UZB/TKM",
    "LNGRSDTJK":  "Work TJK",
    "LNGWORKTJK": "Cargo drivers TJK",
}

async def tg_send(text: str):
    try:
        await asyncio.to_thread(broadcast, text.replace("<b>", "").replace("</b>", ""))                               
    except Exception as e:
        print(f"  tg_send fail: {e}", flush=True)

async def check_slot(sess: dict, cat: str):
    body = {"countryCode": "uzb", "missionCode": "lva", "vacCode": "TAS",
            "visaCategoryCode": cat, "roleName": sess.get("roleName") or "Individual",
            "loginUser": sess["loginUser"], "payCode": ""}
    h = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "uz,en-US;q=0.9,en;q=0.8",
        "authorize": sess["jwt"], "clientsource": cs_now(),
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://visa.vfsglobal.com", "priority": "u=1, i",
        "referer": "https://visa.vfsglobal.com/", "route": "uzb/en/lva",
        "sec-ch-ua": sess.get("sec_ch_ua", '"Chromium";v="136"'), "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": sess.get("sec_ch_ua_platform", '"Windows"'),
        "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-site",
        "user-agent": sess["user_agent"],
    }
    async with AsyncSession(impersonate=sess.get("impersonate", "chrome136"),
                            proxies={"http": sess["proxy"], "https": sess["proxy"]},
                            verify=False) as s:
        try:
            r = await s.post(f"{LIFT_API}/appointment/CheckIsSlotAvailable",
                             headers=h, json=body, cookies=sess["cookies"], timeout=25)
            try:
                return r.status_code, json.loads(r.text), r.headers.get("x-amz-apigw-id", "-")
            except json.JSONDecodeError:
                return r.status_code, r.text[:300], r.headers.get("x-amz-apigw-id", "-")
        except Exception as e:
            return -1, f"{type(e).__name__}: {e}", "-"

def load_pool() -> list[dict]:
    accs = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
    pool = []
    for a in accs:
        if a.get("status", "").startswith("banned"):
            continue
        sess_path = DATA / f"session_{a['email'].replace('@', '_at_')}.json"
        s = None
        if sess_path.exists():
            cand = json.loads(sess_path.read_text())
            age = time.time() - cand.get("login_at", cand.get("captured_at", 0))
            if age < JWT_TTL:
                s = cand
        pool.append({
            "id": a["id"], "email": a["email"], "password": a["password"],
            "proxy": a.get("proxy"),                                  
            "session": s,
            "last_login": s.get("login_at", s.get("captured_at", 0)) if s else 0,
            "dead_until": 0,
        })
    return pool

async def ensure_session(acc: dict, ctx: str = "") -> bool:
                                                                                      
    if acc["session"]:
        age = time.time() - acc["last_login"]
        if age < JWT_TTL and time.time() >= acc["dead_until"]:
            return True
                
    if time.time() < acc["dead_until"]:
        return False
    since_login = time.time() - acc["last_login"]
    if since_login < LOGIN_COOLDOWN:
        return False
    print(f"  [{acc['id']}] {ctx} login...", flush=True)
    new = await do_login(acc["email"], acc["password"], fixed_proxy=acc.get("proxy"))
    if new:
        new["login_at"] = int(time.time())
        acc["session"] = new
        acc["last_login"] = time.time()
        acc["dead_until"] = 0
        return True
                                                                                      
                                                  
    acc["dead_until"] = time.time() + DEAD_COOLDOWN
    acc["last_login"] = time.time()
    return False

def alive_accs(pool: list[dict]) -> list[dict]:
    now = time.time()
    return [a for a in pool if a["session"] and now - a["last_login"] < JWT_TTL and now >= a["dead_until"]]

def best_acc(pool: list[dict]) -> dict | None:
                                                                   
    alive = alive_accs(pool)
    if not alive:
        return None
    alive.sort(key=lambda a: a.get("last_used", 0))
    return alive[0]

async def main_loop(interval: int):
    sys.stdout.reconfigure(line_buffering=True)
    pool = load_pool()
    if not pool:
        print("Empty pool", flush=True); return

                                                                             
                                                                        
    ready = [a for a in pool if a["session"]]
    print(f"[+] pool loaded: {len(pool)} accs, {len(ready)} с живой сессией | "
          f"TARGET_LIVE={TARGET_LIVE} | очередь-ротация", flush=True)

    state_path = DATA / "auto_state_pool.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    async def process_check(acc, code, ts, iters, state, state_path):
                                                                    
        name = CATEGORIES[code]
        try:
            status, resp, amz = await check_slot(acc["session"], code)
            layer = "ORIGIN" if amz != "-" else "CF"
            if status == 200 and isinstance(resp, dict):
                acc["cf429_streak"] = 0                                                          
                _ns["cats"][code] = "200"
                if code == CONTROL_CAT:
                    _ns["control_ok"] = not bool(resp.get("error"))
                if resp.get("error"):
                    err = resp["error"]
                    print(f"[{ts}] iter#{iters} [{NODE}/{acc['id']}] {code} → 200 {layer} | err={err.get('code')} {err.get('description')}", flush=True)
                else:
                    earliest = resp.get("earliestDate")
                    slots = resp.get("earliestSlotLists") or []
                    prev = set(state.get(code, []))
                    new_dates = [s.get("date") for s in slots if s.get("date") and s["date"] not in prev]
                    if earliest and earliest not in prev:
                        new_dates.append(earliest)
                    new_dates = list(set(new_dates))
                    if new_dates:
                        state[code] = list(prev | set(new_dates))
                        state_path.write_text(json.dumps(state, indent=2))
                        if code != CONTROL_CAT:                            
                            await tg_send(f"🎯 <b>НОВЫЕ СЛОТЫ [{NODE}]</b>\n<b>{name}</b> ({code})\n"
                                          f"earliest: {earliest}\nnew: {', '.join(new_dates)}\n"
                                          f"slots: {len(slots)}\nacc: {acc['id']}")
                        print(f"[{ts}] iter#{iters} [{NODE}/{acc['id']}] {code} → {'🧪CTRL' if code==CONTROL_CAT else '🎯NEW'}: {new_dates}", flush=True)
                    else:
                        print(f"[{ts}] iter#{iters} [{NODE}/{acc['id']}] {code} → 200 {layer} | earliest={earliest} slots={len(slots)}", flush=True)
            elif status == 401 and isinstance(resp, dict) and resp.get("code") == "401101":
                print(f"[{ts}] iter#{iters} [{acc['id']}] {code} → 401101 JWT dead → релогин ~300с", flush=True)
                acc["session"] = None
                acc["dead_until"] = time.time() + REST                                                    
            elif status in (401, 403, 429):
                code_inner = resp.get("code") if isinstance(resp, dict) else None
                print(f"[{ts}] iter#{iters} [{acc['id']}] {code} → {status} {layer} code={code_inner} | {str(resp)[:120]}", flush=True)
                if code_inner in ("429201", "429001") or status == 429:
                    acc["cf429_streak"] = acc.get("cf429_streak", 0) + 1
                    if code_inner == "429201" and acc["cf429_streak"] >= CF429_ROTATE:
                                                                                               
                                                                                            
                                                                                                    
                        acc["proxy"] = _rotate_sid(acc["proxy"])
                        acc["session"] = None
                                                                                            
                                                                                                 
                                                                                         
                        acc["dead_until"] = time.time() + REST_429
                        acc["cf429_streak"] = 0
                        print(f"[{ts}] iter#{iters} [{acc['id']}] {CF429_ROTATE}× 429201 подряд → "
                              f"ротация IP (свежий sid), релогин очередью", flush=True)
                    else:
                                                                                           
                                                           
                        acc["dead_until"] = time.time() + 90
                elif status == 401:
                    acc["session"] = None
                    acc["dead_until"] = time.time() + REST                                     
                else:
                    acc["dead_until"] = time.time() + 180                 
            elif status == -1:
                print(f"[{ts}] iter#{iters} [{acc['id']}] {code} → ERR {resp}", flush=True)
                acc["dead_until"] = time.time() + 60
            else:
                print(f"[{ts}] iter#{iters} [{acc['id']}] {code} → HTTP {status} {layer} | {str(resp)[:120]}", flush=True)
        except Exception as e:
            print(f"[{ts}] iter#{iters} ERR {type(e).__name__}: {e}", flush=True)

    cats = list(CATEGORIES)
    iters = 0
    node_last_login = 0.0                                                            
    login_fails = 0                                                       
    login_freeze_until = 0.0
    while True:
        iters += 1
        ts = time.strftime("%H:%M:%S")
        now = time.time()
        alive = alive_accs(pool)
        held = sum(1 for a in pool if a["session"])                                          

                                                                                                     
                                                                                                    
                                                                                  
                                                                                           
                                                                                            
                                                                         
        if held < TARGET_LIVE and now >= login_freeze_until and (now - node_last_login) >= MIN_LOGIN_GAP:
            cand = sorted([a for a in pool if not a["session"] and now >= a["dead_until"]],
                          key=lambda a: a["last_login"])
            if cand:
                ok = await ensure_session(cand[0], "fill")
                node_last_login = time.time()
                if ok:
                    login_fails = 0
                else:
                    login_fails += 1
                    if login_fails >= LOGIN_FAIL_LIMIT:
                        login_freeze_until = now + LOGIN_FREEZE
                        login_fails = 0
                        _ns["login_frozen_until"] = int(login_freeze_until)
                        print(f"⚠️ {LOGIN_FAIL_LIMIT} логинов подряд провалились → ЗАМОРОЗКА логина "
                              f"на {LOGIN_FREEZE // 60}мин (бережём кап; IP ноды похоже в CF-бане)", flush=True)
                alive = alive_accs(pool)

                                                                                       
                                                                                        
                                                                                         
                                                                                        
                                                            
        ncat = len(cats)
        due = [a for a in alive if (now - a.get("last_used", 0)) >= POLL_GAP]
        if due:
            k = min(len(due), ncat)                                                      
            assign = [(due[i], cats[(iters + i) % ncat]) for i in range(k)]
            for a, _c in assign:
                a["last_used"] = now
            await asyncio.gather(*[process_check(a, c, ts, iters, state, state_path) for a, c in assign])

        _ns["accts"] = len(pool); _ns["live"] = len(alive_accs(pool)); write_node_state()
                                                                                              
                                                 
        base = interval if len(alive) >= TARGET_LIVE else REFILL_SLEEP
        await asyncio.sleep(max(1.0, base + random.uniform(-3, 3)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()
    asyncio.run(main_loop(args.interval))

if __name__ == "__main__":
    main()
