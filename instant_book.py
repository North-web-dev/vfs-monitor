

import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, "/opt/vfs-monitor")
from jwt_pool import get_freshest_jwt
from curl_cffi import requests

ROOT = Path("/opt/vfs-monitor")
sys.path.insert(0, "/opt/vfs-monitor")
from tg_notifier import broadcast as tg_broadcast
from proxy_pool import random_proxy

def LOG(*a):
    print("[" + time.strftime("%H:%M:%S") + "]", "instant_book:", *a, flush=True)

def tg(text):
    try:
        n = tg_broadcast(text)
        LOG("TG sent to " + str(n) + " subs")
    except Exception as e:
        LOG("TG fail: " + str(e))

def warmup_cf(proxy_session, cookies):
                                                                                    
    headers = {
        "accept": "application/json",
        "origin": "https://visa.vfsglobal.com",
        "referer": "https://visa.vfsglobal.com/",
        "route": "uzb/en/lva",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    }
    try:
        r = proxy_session.get(
            "https://lift-api.vfsglobal.com/master/center/lva/uzb",
            headers=headers, timeout=10,
            cookies=cookies or {},
            impersonate="chrome120",
        )
                           
        new_cookies = dict(cookies or {})
        for c in r.cookies.jar:
            new_cookies[c.name] = c.value
        LOG("warmup status=" + str(r.status_code) + " new_cookies=" + str([k for k in new_cookies.keys()]))
        return new_cookies
    except Exception as e:
        LOG("warmup fail: " + str(e))
        return cookies or {}

def check_slot(cat, jwt, email, cookies=None):
                                                                                
    body = {
        "countryCode": "uzb",
        "missionCode": "lva",
        "vacCode": "TAS",
        "visaCategoryCode": cat,
        "roleName": "Individual",
        "loginUser": email,
        "payCode": "",
    }
    headers = {
        "accept": "application/json, text/plain, */*",
        "authorize": jwt,
        "cfmlift": "mobile",
        "content-type": "application/json; charset=utf-8",
        "origin": "https://visa.vfsglobal.com",
        "referer": "https://visa.vfsglobal.com/",
        "route": "uzb/en/lva",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    }
    try:
                                                     
        with requests.Session() as s:
            proxy = random_proxy()
            s.proxies = {"http": proxy, "https": proxy}
            warmed_cookies = warmup_cf(s, cookies)
            r = s.post(
                "https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable",
                json=body, headers=headers,
                timeout=15, impersonate="chrome120",
                cookies=warmed_cookies,
            )
            return {"status": r.status_code, "body": r.text, "headers": dict(r.headers)}
    except Exception as e:
        return {"status": "EXC", "body": str(e)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", required=True, help="visaCategoryCode (e.g. LSHMEDCL)")
    args = ap.parse_args()

    LOG("triggered for cat=" + args.cat)

    fresh = get_freshest_jwt()
    if not fresh:
        msg = "🔴 instant_book NO JWT available for " + args.cat
        LOG(msg)
        tg(msg)
        return 1

    LOG("using JWT from " + fresh["email"] + " age=" + str(int(fresh["age_min"])) + "min")
    cookies = fresh["session"].get("cookies", {})

    res = check_slot(args.cat, fresh["jwt"], fresh["email"], cookies)
    LOG("status=" + str(res["status"]) + " body=" + str(res.get("body", ""))[:300])

    try:
        body_json = json.loads(res["body"])
        earliest = body_json.get("earliestDate")
        if earliest:
            msg = (
                "🎯🎯🎯 SLOT CONFIRMED 🎯🎯🎯\n"
                + "cat: " + args.cat + "\n"
                + "earliestDate: " + str(earliest) + "\n"
                + "via acc: " + fresh["email"] + "\n"
                + "full: " + str(body_json)[:400]
            )
            tg(msg)
                                                  
            return 0
        else:
            err = body_json.get("error") or {}
            code = err.get("code")
            msg = (
                "⚠️ slot found by hunter but CheckIsSlotAvailable says no earliestDate\n"
                + "cat: " + args.cat + "\n"
                + "err: " + str(code) + " " + str(err.get("description", "")) + "\n"
                + "via: " + fresh["email"] + " (age=" + str(int(fresh["age_min"])) + "min)"
            )
            tg(msg)
            return 1
    except Exception as e:
        LOG("parse err: " + str(e))
        tg("⚠️ instant_book " + args.cat + " parse err: " + str(e) + " body: " + str(res.get("body", ""))[:200])
        return 1

if __name__ == "__main__":
    sys.exit(main())
