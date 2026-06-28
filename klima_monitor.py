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

import gzip
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

# --- toom: andere API (api.toom.de). sap_id != sku! Geraete sind "nicht online bestellbar"
# -> reine Markt-Play. buyboxcases-POST prueft ALLE Maerkte in EINER Anfrage. ---
TOOM_PRODUCTS = [
    {"name": "Midea PortaSplit Cool 8.000 BTU", "sap_id": "10515238",
     "url": "https://toom.de/p/split-klimaanlage-portasplit-cool-8000btuh/10515238"},
    {"name": "Midea PortaSplit 12.000 BTU", "sap_id": "10272593",
     "url": "https://toom.de/p/mobiles-klimageraet-portasplit-12000-btuh/9350668"},
]
TOOM_MARKETS = [
    {"id": 3609, "name": "Neumarkt"},
    {"id": 3542, "name": "Burglengenfeld"},
    {"id": 3097, "name": "Regensburg-Königswiesen"},
    {"id": 3600, "name": "München-Moosach"},
    {"id": 3603, "name": "Fürstenfeldbruck"},
    {"id": 3601, "name": "München-Neuaubing"},
    {"id": 3602, "name": "München-Haidhausen"},
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


def http_json(url, data=None, timeout=20):
    """GET (data=None) oder POST (data=Objekt -> JSON-Body). Behandelt gzip (toom liefert gzip)."""
    headers = {"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"}
    payload = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8", errors="replace"))


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


def check_obi(state, new_state):
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


def check_toom(state, new_state):
    for p in TOOM_PRODUCTS:
        sap, name = p["sap_id"], p["name"]

        # Markt-Verfuegbarkeit: EIN POST fuer alle Maerkte. state != "unavailable" = Bestand.
        body = [{"market_id": m["id"], "sap_id": sap} for m in TOOM_MARKETS]
        try:
            res = http_json("https://api.toom.de/public/v1/buyboxcases", data=body)
        except Exception as exc:  # noqa: BLE001
            log(f"toom {name}: buyboxcases-Fehler ({exc})")
            res = []
        st_by_market = {r.get("market_id"): r.get("state") for r in res if isinstance(r, dict)}

        for m in TOOM_MARKETS:
            st = st_by_market.get(m["id"])
            available = st is not None and st != "unavailable"
            skey = f"toom|{sap}|store|{m['id']}"
            prev = bool(state.get(skey))
            new_state[skey] = [m["name"]] if available else []
            if available and not prev:
                log(f"toom {name}: MARKT-RESTOCK {m['name']} (state={st})")
                ntfy_push(
                    f"KLIMA toom {m['name']}",
                    f"❄️🔥 {name} im toom {m['name']} verfügbar!\nReservieren & abholen → toom",
                    p["url"],
                )

        # Online (jsonview deliver.state); diese Geraete sind meist "not purchasable online"
        online = False
        try:
            jv = http_json(f"https://api.toom.de/public/v1/jsonview/{sap}/{TOOM_MARKETS[0]['id']}")
            dstate = (jv.get("deliver") or {}).get("state", "")
            online = dstate not in ("not purchasable online", "unavailable", "")
        except Exception as exc:  # noqa: BLE001
            log(f"toom {name}: jsonview-Fehler ({exc})")
        okey = f"toom|{sap}|online"
        prev_online = bool(state.get(okey))
        new_state[okey] = ["online"] if online else []
        if online and not prev_online:
            log(f"toom {name}: ONLINE-RESTOCK!")
            ntfy_push(
                f"KLIMA ONLINE toom: {name}",
                f"❄️🔥 {name} ist ONLINE bei toom lieferbar!\nJetzt bestellen → toom",
                p["url"],
            )

        n = sum(1 for k, v in new_state.items() if k.startswith(f"toom|{sap}|store|") and v)
        log(f"toom {name}: online={'JA' if online else 'nein'} | Maerkte mit Bestand: {n}")


def run_once():
    state = load_state()
    new_state = dict(state)
    check_obi(state, new_state)
    check_toom(state, new_state)
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
