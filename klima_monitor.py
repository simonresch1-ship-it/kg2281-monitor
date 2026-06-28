#!/usr/bin/env python3
"""
OBI-Restock-Monitor fuer die heiss gefragten Midea PortaSplit Klimageraete.
Ueberwacht ONLINE-Lieferbarkeit UND Markt-Verfuegbarkeit (Ingolstadt + Muenchen)
ueber OBIs offene Availability-API (simpler GET, kein Bot-Block -> cloud-faehig 24/7).

API:  https://www.obi.de/api/pdp/v1/availability/<sku>?postalCode=<PLZ>&quantity=1&lang=de-DE
      -> { "deliveryDataPerSeller": [...],  # nicht leer = online lieferbar
           "pickupStores": [...] }          # nicht leer = Markt(e) im PLZ-Umkreis mit Bestand

Ping per ntfy NUR beim Uebergang "nicht da -> da". State in klima_state.json.
NTFY_TOPIC aus Umgebung (GitHub-Secret).
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else ""

PRODUCTS = [
    {"name": "Midea PortaSplit Cool 8.000 BTU", "sku": "2191158911022",
     "url": "https://www.obi.de/p/2191158911022/midea-split-klimaanlage-portasplit-cool-mobil-weissgrau"},
    {"name": "Midea PortaSplit 12.000 BTU", "sku": "8620890",
     "url": "https://www.obi.de/p/8620890/midea-mobile-split-klimaanlage-portasplit"},
]
REGIONS = [
    {"plz": "85049", "label": "Ingolstadt"},
    {"plz": "80331", "label": "Muenchen"},
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "klima_state.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def log(msg):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  {msg}", flush=True)


def http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def ntfy_push(title, message, click_url):
    if not NTFY_URL:
        log("   !! NTFY_TOPIC nicht gesetzt -- kein Push")
        return
    req = urllib.request.Request(NTFY_URL, data=message.encode("utf-8"), method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", "urgent")
    req.add_header("Tags", "snowflake,fire")
    req.add_header("Click", click_url)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        log(f"   -> ntfy-Push raus: {title}")
    except urllib.error.URLError as exc:
        log(f"   !! ntfy-Push fehlgeschlagen: {exc}")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def store_names(pickup_stores):
    """Beste-Muehe-Extraktion der Marktnamen (Feldnamen variieren -> mehrere Kandidaten)."""
    out = []
    for s in pickup_stores:
        if not isinstance(s, dict):
            continue
        name = (s.get("name") or s.get("storeName") or s.get("displayName")
                or s.get("market") or s.get("city") or s.get("town") or "Markt")
        out.append(str(name))
    return out


def run_once():
    state = load_state()
    new_state = dict(state)  # Online-/Regions-Keys einzeln aktualisieren

    for p in PRODUCTS:
        sku, name = p["sku"], p["name"]
        online_available = False

        for region in REGIONS:
            plz, label = region["plz"], region["label"]
            url = f"https://www.obi.de/api/pdp/v1/availability/{sku}?postalCode={plz}&quantity=1&lang=de-DE"
            try:
                data = http_get_json(url)
            except Exception as exc:  # noqa: BLE001 -- Lauf darf nie crashen
                log(f"{name} [{label}]: Fehler ({exc}) -- uebersprungen")
                continue

            if data.get("deliveryDataPerSeller"):
                online_available = True

            pickup = data.get("pickupStores") or []
            key = f"{sku}|{plz}"
            prev = set(state.get(key, []))
            names = store_names(pickup)
            now = set(names)
            new_state[key] = sorted(now)

            newly = sorted(now - prev)
            if newly:
                log(f"{name} [{label}]: MARKT-RESTOCK! {', '.join(newly)} | roh: {json.dumps(pickup)[:300]}")
                ntfy_push(
                    f"KLIMA Markt {label}: {name}",
                    f"❄️🔥 {name} im Markt verfuegbar ({label})!\nMarkt/Markt(e): {', '.join(newly)}\nReservieren & abholen → OBI",
                    p["url"],
                )
            elif now:
                log(f"{name} [{label}]: Markt-Bestand: {', '.join(sorted(now))}")
            else:
                log(f"{name} [{label}]: kein Markt-Bestand")

        # Online-Status (produktweit)
        okey = f"{sku}|online"
        prev_online = bool(state.get(okey))
        new_state[okey] = ["online"] if online_available else []
        if online_available and not prev_online:
            log(f"{name}: ONLINE-RESTOCK!")
            ntfy_push(
                f"KLIMA ONLINE: {name}",
                f"❄️🔥 {name} ist ONLINE wieder lieferbar bei OBI!\nJetzt bestellen → OBI",
                p["url"],
            )
        elif online_available:
            log(f"{name}: online lieferbar")
        else:
            log(f"{name}: online nicht lieferbar")

    save_state(new_state)


def send_test():
    log("Sende Klima-Test-Push ...")
    ntfy_push(
        "KLIMA-Monitor laeuft",
        "✅ OBI-Klima-Monitor ist live (8k + 12k BTU, online + Ingolstadt + Muenchen, 24/7).",
        "https://www.obi.de/p/8620890/midea-mobile-split-klimaanlage-portasplit",
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        send_test()
    else:
        run_once()
