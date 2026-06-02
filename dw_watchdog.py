                      
                                                                                        
                                                                  
import sys, os, time, json
sys.path.insert(0, "/opt/vfs-monitor")
from tg_notifier import broadcast

NODE = os.environ.get("NODE_NAME", "?")
STATE = f"/opt/vfs-monitor/data/datewatch_{NODE}.json"
FLAG = f"/opt/vfs-monitor/data/dw_watchdog_{NODE}.flag"
STALE = 1800                                           

try:
    age = time.time() - os.path.getmtime(STATE)
except Exception:
    age = 10 ** 9

                                                           
stud_ok = False
try:
    d = json.load(open(STATE))
    stud_ok = bool((d.get("cats") or {}).get("LNGSTUD"))
except Exception:
    pass

alerted = os.path.exists(FLAG)
blind = age > STALE or not stud_ok

if blind and not alerted:
    why = f"нет чтения {int(age/60)}мин" if age > STALE else "Студ-контроль пуст (софтблок/слепота)"
    broadcast(f"⚠️ VFS [{NODE}]: датавотч ОСЛЕП — {why}. Проверь прокси/Capsolver/сервис.")
    open(FLAG, "w").write(str(int(time.time())))
elif not blind and alerted:
    broadcast(f"✅ VFS [{NODE}]: датавотч восстановился, снова читает слоты (Студ ок).")
    try:
        os.remove(FLAG)
    except Exception:
        pass
