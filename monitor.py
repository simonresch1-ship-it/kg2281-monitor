#!/usr/bin/env python3
"""
Cloud-Restock-Monitor (GitHub Actions) fuer die adidas Deutschland EQT
Trainingsjacke KG2281 (Equipment Green) + die Breuninger-Variante.

Laeuft alle ~5 Min als GitHub-Action, voellig unabhaengig vom Mac.
Ueberwacht 3 Shopify-Shops (.js-Endpoint, available-Flag pro Groesse) und
Breuninger (eingebettetes State-JSON, "stock"-Integer pro Groesse).

Ping per ntfy.sh -- NUR beim Uebergang "ausverkauft -> verfuegbar".
Der Zustand liegt in state.json und wird vom Workflow ins Repo zurueckcommittet,
damit zwischen den Laeufen kein Spam und keine verpassten Restocks entstehen.

NTFY_TOPIC kommt aus der Umgebung (GitHub-Secret).
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import html as ihtml
from datetime import datetime, timezone

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else ""

SHOPS = [
    {
        "name": "Overkill",
        "type": "shopify",
        "fetch_url": "https://www.overkillshop.com/products/adidas-dfb-germany-equipment-track-top-kg2281-equipment-green.js",
        "buy_url": "https://www.overkillshop.com/products/adidas-dfb-germany-equipment-track-top-kg2281-equipment-green",
    },
    {
        "name": "Asphaltgold",
        "type": "shopify",
        "fetch_url": "https://www.asphaltgold.com/products/adidasdfb-germany-eqt-track-top-equipment-green.js",
        "buy_url": "https://www.asphaltgold.com/de/products/adidasdfb-germany-eqt-track-top-equipment-green",
    },
    {
        "name": "Schrittmacher",
        "type": "shopify",
        "fetch_url": "https://www.schrittmacher-shop.com/products/adidas-germany-eqt-track-top-equipment-green-kg2281.js",
        "buy_url": "https://www.schrittmacher-shop.com/products/adidas-germany-eqt-track-top-equipment-green-kg2281",
    },
    {
        "name": "Smart Dresser",
        "type": "shopify",
        "fetch_url": "https://www.thesmartdresser.de/products/dfb-eqt-tracktop-eqt-green.js",
        "buy_url": "https://www.thesmartdresser.de/products/dfb-eqt-tracktop-eqt-green",
    },
    {
        "name": "footdistrict",
        "type": "shopify",
        "fetch_url": "https://footdistrict.com/products/adidas-originals-eqt-germany-football-logo-relaxed-mens-track-jacket-kg2281.js",
        "buy_url": "https://footdistrict.com/en/products/adidas-originals-eqt-germany-football-logo-relaxed-mens-track-jacket-kg2281",
        "note": "⚠️ 2 Stück mitnehmen → Gratisversand (frei erst ab 180 €)",
    },
    {
        "name": "Orange Jungle",
        "type": "shopify",
        "fetch_url": "https://www.orangejungle.de/products/dfb-equipment-track-top.js",
        "buy_url": "https://www.orangejungle.de/products/dfb-equipment-track-top",
        "note": "⚠️ 2 Stück mitnehmen → Gratisversand (frei erst ab 100 €, Jacke 99,99 €)",
    },
    {
        "name": "Breuninger",
        "type": "breuninger",
        "fetch_url": "https://www.breuninger.com/de/marken/adidas/trainingsjacke-equipment-tt/1003077483/p/?variant=209ffbd7d7a540838023de0d0b3dc3f2",
        "buy_url": "https://www.breuninger.com/de/marken/adidas/trainingsjacke-equipment-tt/1003077483/p/?variant=209ffbd7d7a540838023de0d0b3dc3f2",
    },
    # --- 2. Produkt: adidas x Willy Chavarria WCC Soccer Jersey (KU7803, gruen) ---
    {
        "name": "Overkill",
        "product": "WCC Jersey (KU7803)",
        "type": "shopify",
        "fetch_url": "https://www.overkillshop.com/products/willy-chavarria-x-adidas-soccer-jersey-ku7803-collegiate-green.js",
        "buy_url": "https://www.overkillshop.com/products/willy-chavarria-x-adidas-soccer-jersey-ku7803-collegiate-green",
    },
    {
        "name": "footdistrict",
        "product": "WCC Jersey (KU7803)",
        "type": "shopify",
        "fetch_url": "https://footdistrict.com/products/adidas-originals-x-willy-chavarria-logo-half-sleeved-oversize-mens-jersey-t-shirt-ku7803.js",
        "buy_url": "https://footdistrict.com/en/products/adidas-originals-x-willy-chavarria-logo-half-sleeved-oversize-mens-jersey-t-shirt-ku7803",
        "note": "ℹ️ Versand frei ab 180 € — Jersey liegt bei 180 €, ggf. knapp drunter",
    },
    # --- 3. Produkt: Deutschland Aufwaermtrikot 2026 Heim (nur Breuninger, M-XXL) ---
    {
        "name": "Breuninger",
        "product": "DE Aufwärmtrikot 2026 Heim",
        "type": "breuninger",
        "fetch_url": "https://www.breuninger.com/de/marken/adidas/aufwaermtrikot-deutschland-2026-heim/1002965160/p/?variant=66c0629853c74b3999f5b88ccac325d1",
        "buy_url": "https://www.breuninger.com/de/marken/adidas/aufwaermtrikot-deutschland-2026-heim/1002965160/p/?variant=66c0629853c74b3999f5b88ccac325d1",
        "note": "⚠️ Immer nur 2 Stück nehmen",
    },
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  {msg}", flush=True)


def http_get(url: str, timeout: int = 25) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def ntfy_push(title: str, message: str, click_url: str,
              priority: str = "urgent", tags: str = "fire,shoe") -> None:
    if not NTFY_URL:
        log("   !! NTFY_TOPIC nicht gesetzt -- kein Push")
        return
    req = urllib.request.Request(NTFY_URL, data=message.encode("utf-8"), method="POST")
    req.add_header("Title", title)          # nur ASCII -- Emojis in message/tags
    req.add_header("Priority", priority)
    req.add_header("Tags", tags)
    req.add_header("Click", click_url)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        log(f"   -> ntfy-Push raus: {title}")
    except urllib.error.URLError as exc:
        log(f"   !! ntfy-Push fehlgeschlagen: {exc}")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def available_sizes_shopify(text: str) -> list:
    data = json.loads(text)
    return [v.get("title", "?") for v in data.get("variants", []) if v.get("available")]


def available_sizes_breuninger(text: str) -> list:
    txt = ihtml.unescape(text)
    out = []
    for m in re.finditer(r'"size"\s*:\s*"([^"]+)"\s*,\s*"stock"\s*:\s*(\d+)', txt):
        if int(m.group(2)) > 0:
            out.append(m.group(1))
    return sorted(set(out))


def available_sizes(text: str, shop_type: str) -> list:
    if shop_type == "breuninger":
        return available_sizes_breuninger(text)
    return available_sizes_shopify(text)


# Nur diese Groessen sollen pingen (Reseller-relevant). 2XL wird als XXL gewertet.
WANTED_SIZES = {"M", "L", "XL", "XXL"}


def size_in_scope(variant_title: str) -> bool:
    """True, wenn im Varianten-Titel eine gewuenschte Groesse als eigenes Token steckt.
    Tokenisiert ueber Nicht-Alphanumerik, damit XL nicht faelschlich in XXL matcht."""
    for tok in re.split(r"[^A-Za-z0-9]+", variant_title):
        t = tok.upper()
        if t == "2XL":
            t = "XXL"
        if t in WANTED_SIZES:
            return True
    return False


def run_once() -> None:
    state = load_state()
    new_state = {}
    for shop in SHOPS:
        name = shop["name"]
        product = shop.get("product", "DFB EQT Jacke (KG2281)")
        key = f"{name}|{product}"   # eindeutig pro Produkt+Shop (kein Kollidieren)
        try:
            body = http_get(shop["fetch_url"])
            avail = available_sizes(body, shop["type"])
            avail = [s for s in avail if size_in_scope(s)]  # nur M/L/XL/XXL
        except Exception as exc:  # noqa: BLE001 -- Lauf darf nie crashen
            log(f"{name} [{product}]: Fehler ({exc}) -- uebersprungen")
            if key in state:
                new_state[key] = state[key]
            continue

        prev = set(state.get(key, []))
        now = set(avail)
        new_state[key] = sorted(now)

        newly = sorted(now - prev)
        if newly:
            sizes = ", ".join(newly)
            log(f"{name} [{product}]: RESTOCK! Neu verfuegbar: {sizes}")
            msg = f"🔥 {product} wieder da!\nGröße(n): {sizes}\nJetzt zuschlagen bei {name}"
            if shop.get("note"):
                msg += f"\n{shop['note']}"
            ntfy_push(
                title=f"RESTOCK {name}: {product}",
                message=msg,
                click_url=shop["buy_url"],
            )
        else:
            log(f"{name} [{product}]: {', '.join(sorted(now)) if now else 'alles ausverkauft'}")

    save_state(new_state)


def send_test() -> None:
    log("Sende Test-Push ...")
    ntfy_push(
        title="KG2281 Cloud-Monitor laeuft",
        message="✅ GitHub-Actions-Monitor ist live. Laeuft jetzt 24/7 alle ~5 Min, "
                "unabhaengig vom Mac. Ping kommt nur noch bei echtem Restock.",
        click_url="https://www.breuninger.com/de/marken/adidas/trainingsjacke-equipment-tt/1003077483/p/",
        priority="default",
        tags="white_check_mark",
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        send_test()
    else:
        run_once()
