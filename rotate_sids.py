

import re, sys, secrets, glob, os
import yaml
CFG = "/opt/vfs-monitor/config/accounts.yaml"
d = yaml.safe_load(open(CFG))
n = 0
for a in d["accounts"]:
    p = a.get("proxy")
    if not p: continue
    new = secrets.token_hex(7)                            
    p2 = re.sub(r"(-sid-)[A-Za-z0-9]+(-)", r"\g<1>%s\g<2>" % new, p, count=1)
    if p2 != p:
        a["proxy"] = p2; n += 1
yaml.safe_dump(d, open(CFG, "w"), sort_keys=False, allow_unicode=True)
print(f"rotated {n} sids")
                                                             
for f in glob.glob("/opt/vfs-monitor/data/sessions*.json") + glob.glob("/opt/vfs-monitor/data/*session*.json"):
    try: os.remove(f); print("removed", f)
    except Exception: pass
