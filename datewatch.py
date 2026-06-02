import os
                      
                                                                                 
import sys, json, asyncio, secrets, time, random, os
sys.path.insert(0, "/opt/vfs-monitor")
from auto_login import auto_login
import multi_auto_run as M
from tg_notifier import broadcast
ROOT="/opt/vfs-monitor"; NODE=os.environ.get("NODE_NAME","?")
WORK={"LSHMEDCL":"Work UZB","LNGWORK":"Cargo UZB","LNGRSDTJK":"Work TJK","LNGWORKTJK":"Cargo TJK"}
STATE=ROOT+f"/data/datewatch_{NODE}.json"
NSESS=int(os.environ.get("NSESS","3")); CYCLE=int(os.environ.get("CYCLE","8")); SESS_TTL=2700; HB_GAP=864000
def px():
    import random as _r
    try:
        _l=[x.strip() for x in open("/opt/vfs-monitor/data/mobile_proxies.txt") if x.strip()]
        if _l: return _r.choice(_l)
    except Exception: pass
    return os.getenv("VFS_PROXY", "")
def load(p,d):
    try: return json.load(open(p))
    except Exception: return d
def short(x): return x.split(" ")[0]
def parse(res):
    if not isinstance(res,dict): return []
    sl=res.get("earliestSlotLists") or []
    dates=[s.get("date") for s in sl if isinstance(s,dict) and s.get("date")]
    ed=res.get("earliestDate")
    if ed and ed not in dates: dates.insert(0,ed)
    return dates
def reg_accts():
    reg=load(ROOT+"/data/registered_accounts.json",[])
    if isinstance(reg,dict): reg=list(reg.values())
    c=[a for a in reg if (a.get("activated") or a.get("jwt_ok")) and a.get("password")]
    random.shuffle(c); return c
async def fresh():
    for a in reg_accts()[:3]:
        try: s=await asyncio.wait_for(auto_login(a["email"],a["password"],fixed_proxy=px()),timeout=70)
        except Exception: s=None
        if s: s["_saved"]=time.time(); return s
    return None
async def check_one(sess,code):
    try: st,res,_=await asyncio.wait_for(M.check_slot(sess,code),timeout=30)
    except Exception: return None,"err"
    if st==401 or (isinstance(res,dict) and res.get("code")=="401101"): return None,"dead"
    if st==429 or (isinstance(res,dict) and res.get("code") in ("429001","429201")): return None,"throttled"
    return parse(res),"ok"
async def check_all(sess):
                                                                        
    sd,stt=await check_one(sess,"LNGSTUD")
    if stt=="dead": return None,"dead"
    if stt=="throttled": return None,"throttled"
    if stt!="ok" or not sd: return None,"softblock"
    out={"LNGSTUD":sd}
    for code in WORK:
        await asyncio.sleep(random.uniform(2,4))                              
        d,s2=await check_one(sess,code)
        if s2=="dead": return None,"dead"
        out[code]=d or []
    return out,"ok"
async def loop():
    st0=load(STATE,{"cats":{},"last_hb":0}); prevcats=st0.get("cats",{}); last_hb=st0.get("last_hb",0)
    sessions=[]; idx=0; fail=0; sbcount=0
    while True:
        try:
            sessions=[s for s in sessions if time.time()-s.get("_saved",0)<SESS_TTL]
            if sessions:
                sess=sessions[idx%len(sessions)]; idx+=1
                res,status=await check_all(sess)
                if status=="throttled":
                    print(f"[{NODE}] 429 throttled -> keep session, backoff (no relogin)",flush=True)
                    await asyncio.sleep(90)
                elif status in ("dead","softblock","err"):
                    try: sessions.remove(sess)
                    except Exception: pass
                    if status=="softblock": sbcount+=1
                    print(f"[{NODE}] session {status} -> drop+refresh (sb={sbcount})",flush=True)
                elif res:
                    lines=[f"Студ: "+(", ".join(short(x) for x in res.get('LNGSTUD',[])) or '—')]
                    realalerts=[]
                    for code,name in WORK.items():
                        d=res.get(code,[]); lines.append(name+": "+(", ".join(short(x) for x in d) if d else "—"))
                        newd=[x for x in d if x not in prevcats.get(code,[])]
                        if newd: realalerts.append("\U0001f3af "+name+": "+", ".join(short(x) for x in newd))
                        prevcats[code]=d
                    prevcats["LNGSTUD"]=res.get("LNGSTUD",[])
                    summary=f"\U0001f4cb VFS [{NODE}] (LVA):\n"+"\n".join(lines)+"\n⏱ "+time.strftime("%d.%m %H:%M:%S")
                    if realalerts:
                        broadcast("\U0001f6a8\U0001f6a8 НОВЫЕ СЛОТЫ! ["+NODE+"]\n"+"\n".join(realalerts)+"\n\n"+summary); last_hb=time.time()
                    elif time.time()-last_hb>=HB_GAP:
                        broadcast(summary); last_hb=time.time()
                    json.dump({"cats":prevcats,"last_hb":last_hb},open(STATE,"w"))
                    print(summary.splitlines()[-1]+" | Студ:"+(",".join(short(x) for x in res.get("LNGSTUD",[])) or "-"),flush=True)
            if len(sessions)<NSESS:
                s=await fresh()
                if s: sessions.append(s); fail=0
                else:
                    fail+=1
                    if fail in (3,10) or fail%30==0: broadcast(f"⚠️ VFS [{NODE}] не логинится {fail}x — нужен взгляд")
        except Exception as e:
            print(f"[{NODE}] loop err: {type(e).__name__}: {e}",flush=True)
        await asyncio.sleep(CYCLE if sessions else min(120*(2**min(fail,4)),1800))
asyncio.run(loop())
