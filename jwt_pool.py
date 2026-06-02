

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
import yaml

ROOT = Path("/opt/vfs-monitor")
DATA = ROOT / "data"

REFRESH_AGE_MIN = 240                                                                           
MIN_REFRESH_GAP_SEC = 900                                                         
LOOP_SLEEP_SEC = 60
COOLDOWN_FILE = DATA / "jwt_pool_cooldowns.json"

                                                                  
INITIAL_COOLDOWN_MIN = 240                                   

def LOG(*a):
    print("[" + time.strftime("%H:%M:%S") + "]", "jwt-pool:", *a, flush=True)

def load_accounts():
    accs = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())["accounts"]
    return [a for a in accs if a.get("status") in ("ok", "untested")]

def session_file(email):
    safe = email.replace("@", "_at_").replace(".", "_").replace("_at_", "_at_")
                                                                 
    return DATA / ("session_" + email.replace("@", "_at_") + ".json")

def load_session(email):
    p = session_file(email)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None

def load_cooldowns():
    if COOLDOWN_FILE.exists():
        try:
            return json.loads(COOLDOWN_FILE.read_text())
        except Exception:
            pass
    return {}

def save_cooldowns(d):
    COOLDOWN_FILE.write_text(json.dumps(d, indent=2))

def is_cooled_down(email, cd):
                                                                         
    rec = cd.get(email)
    if not rec:
        return True
    until = rec.get("until", 0)
    return time.time() >= until

def set_cooldown(email, fails, cd, minutes=None):
    if minutes is None:
                                                                
                                                                                                
        minutes = min(1440, INITIAL_COOLDOWN_MIN * (2 ** (fails - 1)))
    cd[email] = {
        "until": time.time() + minutes * 60,
        "fails": fails,
        "set_at": time.time(),
    }
    save_cooldowns(cd)
    LOG("cooldown set: " + email + " for " + str(minutes) + " min (fails=" + str(fails) + ")")

def clear_cooldown(email, cd):
    if email in cd:
        del cd[email]
        save_cooldowns(cd)

def refresh_account(email, password):
                                                                            
    LOG("refreshing " + email + "...")
    try:
                                                   
        result = subprocess.run(
            ["python3", str(ROOT / "auto_login.py"), "--email", email, "--password", password],
            capture_output=True, text=True, timeout=120,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0 and "ACCESS_TOKEN_CAPTURED" in output:
            LOG("refresh OK: " + email)
            return True, None
                       
        if "429001" in output or "Too Many Requests" in output.lower():
            LOG("refresh 429001: " + email)
            return False, "429001"
                                        
        if "403204" in output:
            LOG("refresh 403204 (banned): " + email)
            return False, "403204"
        LOG("refresh FAIL: " + email + " - " + output[-300:].replace("\n", " "))
        return False, "unknown"
    except subprocess.TimeoutExpired:
        LOG("refresh TIMEOUT: " + email)
        return False, "timeout"
    except Exception as e:
        LOG("refresh EXC " + email + ": " + str(e))
        return False, "exception"

def needs_refresh(sess):
                                             
    if not sess:
        return True
    cap = sess.get("captured_at", 0)
    age_sec = time.time() - cap
    return age_sec > REFRESH_AGE_MIN * 60

def get_freshest_jwt():
                                                                                        
    accs = load_accounts()
    candidates = []
    for a in accs:
        sess = load_session(a["email"])
        if sess and sess.get("jwt"):
            candidates.append((sess.get("captured_at", 0), sess["jwt"], a["email"], sess))
    if not candidates:
        return None
    candidates.sort(reverse=True)                
    cap, jwt, email, sess = candidates[0]
    age_min = (time.time() - cap) / 60
    return {"jwt": jwt, "email": email, "captured_at": cap, "age_min": age_min, "session": sess}

def main():
    LOG("=== jwt_pool start ===")
    last_global_refresh = 0
    while True:
        try:
            accs = load_accounts()
            cd = load_cooldowns()
            now = time.time()
            for a in accs:
                email = a["email"]
                if not is_cooled_down(email, cd):
                    rec = cd[email]
                    remaining = (rec["until"] - now) / 60
                    if int(now) % 300 < LOOP_SLEEP_SEC:                   
                        LOG(email + " cooldown remaining " + str(int(remaining)) + " min (fails=" + str(rec["fails"]) + ")")
                    continue
                sess = load_session(email)
                if not needs_refresh(sess):
                    age = (now - sess["captured_at"]) / 60
                    if int(now) % 300 < LOOP_SLEEP_SEC:
                        LOG(email + " JWT fresh (age=" + str(int(age)) + " min)")
                    continue
                                 
                if now - last_global_refresh < MIN_REFRESH_GAP_SEC:
                    LOG("global throttle: " + email + " refresh queued (next free in " + str(int(MIN_REFRESH_GAP_SEC - (now - last_global_refresh))) + "s)")
                    break
                         
                last_global_refresh = now
                success, err = refresh_account(email, a["password"])
                if success:
                    clear_cooldown(email, cd)
                else:
                    fails = cd.get(email, {}).get("fails", 0) + 1
                    if err == "403204":
                                                       
                        set_cooldown(email, fails, cd, minutes=10080)
                    else:
                        set_cooldown(email, fails, cd)
                break                                              
            time.sleep(LOOP_SLEEP_SEC)
        except KeyboardInterrupt:
            LOG("interrupted")
            sys.exit(0)
        except Exception as e:
            LOG("loop err: " + str(e))
            time.sleep(LOOP_SLEEP_SEC)

def cmd_status():
                                   
    accs = load_accounts()
    cd = load_cooldowns()
    now = time.time()
    print("=== JWT POOL STATUS ===")
    for a in accs:
        email = a["email"]
        sess = load_session(email)
        if sess and sess.get("jwt"):
            age_min = (now - sess.get("captured_at", 0)) / 60
            jwt_preview = sess["jwt"][:20] + "..."
            sess_info = "JWT age=" + str(int(age_min)) + "min token=" + jwt_preview
        else:
            sess_info = "NO_SESSION"
        rec = cd.get(email)
        if rec and rec["until"] > now:
            cd_info = "cooldown=" + str(int((rec["until"] - now) / 60)) + "min fails=" + str(rec["fails"])
        else:
            cd_info = "ready"
        print("  " + a["id"] + " " + email + " | " + sess_info + " | " + cd_info)
    fresh = get_freshest_jwt()
    if fresh:
        print("\nFreshest JWT: " + fresh["email"] + " age=" + str(int(fresh["age_min"])) + "min")
    else:
        print("\nNO FRESH JWT AVAILABLE")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        cmd_status()
    else:
        main()
