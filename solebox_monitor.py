#!/usr/bin/env python3
"""
Cloud-Drop-Monitor fuer solebox-Produkte (24/7, kein Mac noetig).
Signal: Haupt-Produkt-JSON-LD offers.availability == schema.org/InStock (OutOfStock=offline).
Pingt ntfy beim Uebergang offline -> InStock. State in solebox_state.json.

Warum Cloud: solebox-Produktseite ist ein simpler GET (kein Bot-Block fuer die Seite selbst)
-> laeuft von GitHub-IP. Loest das Problem, dass der lokale Watcher bei Mac-Schlaf (Akku) Luecken hatte.
"""
import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else ""

PRODUCTS = [
    {"name": "Jacquemus x Nike France Jersey", "pid": "98276"},
]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "solebox_state.json")


def log(msg):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  {msg}", flush=True)


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def availability(html):
    """offers.availability des ERSTEN Product-JSON-LD (=Hauptprodukt; Karussells emittieren keins)."""
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        arr = j if isinstance(j, list) else (j.get("@graph") if isinstance(j, dict) and "@graph" in j else [j])
        for o in arr:
            if isinstance(o, dict) and o.get("@type") == "Product":
                off = o.get("offers")
                off = off[0] if isinstance(off, list) else off
                av = (off or {}).get("availability", "") if isinstance(off, dict) else ""
                return av, o.get("name", "")
    return None, None


def ntfy_push(title, message, click_url):
    if not NTFY_URL:
        log("   !! NTFY_TOPIC nicht gesetzt")
        return
    req = urllib.request.Request(NTFY_URL, data=message.encode("utf-8"), method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", "urgent")
    req.add_header("Tags", "rotating_light,fire")
    req.add_header("Click", click_url)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        log(f"   -> ntfy-Push raus: {title}")
    except urllib.error.URLError as e:
        log(f"   !! ntfy fehlgeschlagen: {e}")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def run_once():
    state = load_state()
    new_state = dict(state)
    for p in PRODUCTS:
        pid, name = p["pid"], p["name"]
        url = f"https://www.solebox.com/de-de/p/{pid}"
        try:
            av, real_name = availability(http_get(url))
        except Exception as exc:  # noqa: BLE001
            log(f"{name}: Fehler ({exc}) -- uebersprungen")
            continue
        live = bool(av and "InStock" in av)
        key = f"solebox|{pid}"
        prev_live = bool(state.get(key))
        new_state[key] = live
        if live and not prev_live:
            log(f"{name}: DROP LIVE! ({av})")
            ntfy_push("DROP LIVE: solebox", f"🚨 {real_name or name} ist LIVE auf solebox — JETZT coppen!\n{url}", url)
        elif live:
            log(f"{name}: live (schon gemeldet)")
        else:
            log(f"{name}: offline ({av})")
    save_state(new_state)


def send_test():
    log("Test-Push ...")
    ntfy_push("solebox-Monitor laeuft", "✅ Cloud-Drop-Monitor fuer solebox aktiv (24/7).",
              "https://www.solebox.com/de-de/p/98276")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        send_test()
    else:
        run_once()
