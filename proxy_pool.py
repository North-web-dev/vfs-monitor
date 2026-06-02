

import random
from pathlib import Path

ROOT = Path("/opt/vfs-monitor")
PROXIES_FILE = ROOT / "config" / "proxies.txt"

def load_proxies():
                                                               
    proxies = []
    if not PROXIES_FILE.exists():
        return proxies
    for line in PROXIES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            host, port, user, pw = line.split(":", 3)
            proxies.append("http://" + user + ":" + pw + "@" + host + ":" + port)
        except ValueError:
            continue
    return proxies

def random_proxy():
                                                                     
    p = load_proxies()
    if not p:
        return None
    return random.choice(p)

def all_proxies():
    return load_proxies()

if __name__ == "__main__":
    p = load_proxies()
    print(f"Loaded {len(p)} proxies:")
    for proxy in p:
                                   
        masked = proxy.split(":pm")[0] + ":***@" + proxy.split("@")[1]
        print(f"  {masked}")
