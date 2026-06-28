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
# Alle OBI-Maerkte auf der Achse Nuernberg <-> Ingolstadt <-> Muenchen (+ Rand).
# Jeder Markt wird ueber seine eigene PLZ abgefragt; Treffer-Match ueber storeId ODER PLZ
# im pickupStores-Rohtext (strukturagnostisch). Quelle: OBI store-locator country/de.
STORES = [
    {"id": "276", "zip": "91207", "name": "Lauf"},
    {"id": "146", "zip": "91224", "name": "Pommelsbrunn"},
    {"id": "152", "zip": "92237", "name": "Sulzbach-Rosenberg"},
    {"id": "175", "zip": "90766", "name": "Fürth"},
    {"id": "324", "zip": "90552", "name": "Röthenbach"},
    {"id": "351", "zip": "90411", "name": "Nürnberg Äußere Bayreuther Str."},
    {"id": "125", "zip": "90431", "name": "Nürnberg Leyher Straße"},
    {"id": "235", "zip": "92224", "name": "Amberg"},
    {"id": "391", "zip": "90480", "name": "Nürnberg Regensburger Straße"},
    {"id": "165", "zip": "90592", "name": "Schwarzenbruck"},
    {"id": "136", "zip": "91126", "name": "Schwabach"},
    {"id": "304", "zip": "92318", "name": "Neumarkt"},
    {"id": "334", "zip": "91154", "name": "Roth"},
    {"id": "191", "zip": "91171", "name": "Greding"},
    {"id": "144", "zip": "91781", "name": "Weißenburg"},
    {"id": "180", "zip": "85072", "name": "Eichstätt"},
    {"id": "261", "zip": "93326", "name": "Abensberg"},
    {"id": "484", "zip": "86633", "name": "Neuburg"},
    {"id": "301", "zip": "86551", "name": "Aichach"},
    {"id": "401", "zip": "86391", "name": "Stadtbergen (Augsburg)"},
    {"id": "268", "zip": "85221", "name": "Dachau"},
    {"id": "390", "zip": "85599", "name": "Parsdorf"},
    {"id": "244", "zip": "81243", "name": "München Neuaubing"},
    {"id": "445", "zip": "81929", "name": "München-Daglfing"},
    {"id": "190", "zip": "80686", "name": "München-Westend"},
    {"id": "248", "zip": "81827", "name": "München Trudering"},
    {"id": "357", "zip": "82152", "name": "München-Martinsried"},
    {"id": "447", "zip": "86899", "name": "Landsberg am Lech"},
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


def run_once():
    state = load_state()
    new_state = dict(state)

    for p in PRODUCTS:
        sku, name = p["sku"], p["name"]
        online_available = False
        matched = []            # Maerkte mit Bestand (sicherer Match)
        unmatched_raw = None    # pickupStores nicht-leer, aber kein Store-Match -> zum Verfeinern loggen

        for store in STORES:
            url = f"https://www.obi.de/api/pdp/v1/availability/{sku}?postalCode={store['zip']}&quantity=1&lang=de-DE"
            try:
                data = http_get_json(url)
            except Exception as exc:  # noqa: BLE001 -- Lauf darf nie crashen
                log(f"{name} [{store['name']}]: Fehler ({exc})")
                # alten Zustand fuer diesen Markt behalten
                continue

            if data.get("deliveryDataPerSeller"):
                online_available = True

            pickup = data.get("pickupStores") or []
            skey = f"{sku}|store|{store['id']}"
            has_stock = False
            if pickup:
                raw = json.dumps(pickup, ensure_ascii=False)
                if store["id"] in raw or store["zip"] in raw:
                    has_stock = True
                else:
                    unmatched_raw = raw[:500]

            prev = bool(state.get(skey))
            new_state[skey] = [store["name"]] if has_stock else []
            if has_stock and not prev:
                log(f"{name}: MARKT-RESTOCK {store['name']}!")
                ntfy_push(
                    f"KLIMA Markt {store['name']}",
                    f"❄️🔥 {name} im OBI {store['name']} verfuegbar!\nReservieren & abholen → OBI",
                    p["url"],
                )

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

        n_markets = sum(1 for k, v in new_state.items() if k.startswith(f"{sku}|store|") and v)
        log(f"{name}: online={'JA' if online_available else 'nein'} | Maerkte mit Bestand: {n_markets}")
        if unmatched_raw:
            log(f"{name}: HINWEIS pickupStores nicht-leer ohne Store-Match -> RAW: {unmatched_raw}")

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
