import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ============================================================
# AUTODEALSCANNER – KASSEL
# ============================================================

BASE = "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen"
KEY = os.environ.get("KLAZ_API_KEY", "").strip()

# Kassel ist fest hinterlegt
LATITUDE = "51.3127"
LONGITUDE = "9.4797"
DISTANCE_KM = "100"

MIN_PRICE = 100
MAX_PRICE = 7000
MIN_TARGET_PROFIT = 800

STATE_FILE = "data/state.json"
OUTPUT_FILE = "data/results.json"

BRANDS = (
    "volkswagen", "vw", "golf", "polo", "passat",
    "opel", "astra", "corsa",
    "ford", "focus", "fiesta", "mondeo",
    "bmw", "mercedes", "benz", "audi",
    "skoda", "seat", "renault", "peugeot",
    "citroen", "citroën", "dacia", "volvo", "fiat",
    "toyota", "hyundai", "kia", "mazda",
    "honda", "nissan", "suzuki", "mini",
    "mitsubishi", "alfa romeo", "chevrolet",
    "gebrauchtwagen", "pkw"
)

EXCLUDE_WORDS = (
    "anhänger", "autoanhänger", "felgen", "reifen",
    "motoröl", "ersatzteil", "scheinwerfer",
    "getriebeöl", "suche auto", "autoteile",
    "schlachtfest", "nur teile"
)

SEVERE_DEFECTS = (
    "motorschaden", "getriebeschaden", "unfallwagen",
    "bastlerfahrzeug", "nicht fahrbereit",
    "springt nicht an", "kupplung defekt"
)

MINOR_DEFECTS = (
    "tüv abgelaufen", "tuev abgelaufen", "rost",
    "klima defekt", "kratzer", "delle",
    "reparatur", "defekt", "ölverlust",
    "oelverlust"
)


# ============================================================
# DATEIEN
# ============================================================

def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


# ============================================================
# API
# ============================================================

def api(endpoint, params=None):
    if not KEY:
        raise RuntimeError(
            "API-Schlüssel fehlt. Bitte in GitHub unter "
            "Settings > Secrets and variables > Actions "
            "das Secret KLAZ_API_KEY prüfen."
        )

    url = BASE + endpoint

    if params:
        url += "?" + urlencode(params)

    request = Request(
        url,
        headers={
            "klaz_key": KEY,
            "Accept": "application/json",
            "User-Agent": "AutoDealScanner/1.0"
        }
    )

    try:
        with urlopen(request, timeout=45) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

    except HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"API-Fehler HTTP {error.code}: {body[:500]}"
        )

    except URLError as error:
        raise RuntimeError(
            f"Verbindung zur API fehlgeschlagen: {error}"
        )

    except json.JSONDecodeError:
        raise RuntimeError("Die API lieferte keine gültige JSON-Antwort.")

    if result.get("success") is False:
        raise RuntimeError(
            result.get("message", "Die API-Suche ist fehlgeschlagen.")
        )

    return result


# ============================================================
# INSERATE AUSWERTEN
# ============================================================

def price_of(ad):
    price = ad.get("price")

    if isinstance(price, dict):
        price = price.get("amount")

    try:
        return float(price)
    except (ValueError, TypeError):
        return None


def text_of(ad):
    return (
        str(ad.get("title", "")) + " " +
        str(ad.get("description", ""))
    ).lower()


def is_car(ad):
    text = text_of(ad)

    if any(word in text for word in EXCLUDE_WORDS):
        return False

    return any(brand in text for brand in BRANDS)


def defect_reserve(ad, price):
    text = text_of(ad)

    if any(word in text for word in SEVERE_DEFECTS):
        return max(1500, price * 0.35)

    if any(word in text for word in MINOR_DEFECTS):
        return max(900, price * 0.18)

    return max(600, price * 0.10)


def model_key(title):
    """
    Grobe Gruppierung nach Marke und Modell.
    Das ist KEIN verlässlicher Ersatz für Baujahr,
    Motorisierung, Kilometerstand und Ausstattung.
    """

    words = re.sub(
        r"[^a-z0-9äöüß ]",
        " ",
        str(title).lower()
    ).split()

    makes = (
        "volkswagen", "vw", "opel", "ford", "bmw",
        "mercedes", "benz", "audi", "skoda", "seat",
        "renault", "peugeot", "citroen", "citroën",
        "dacia", "volvo", "fiat", "toyota", "hyundai",
        "kia", "mazda", "honda", "nissan", "suzuki",
        "mini", "mitsubishi", "chevrolet"
    )

    for index, word in enumerate(words):
        if word in makes:
            if index + 1 < len(words):
                return word + " " + words[index + 1]
            return word

    return " ".join(words[:2])


def get_location(ad):
    location = ad.get("location") or {}

    if isinstance(location, dict):
        return (
            location.get("name")
            or location.get("city")
            or "Kassel-Umkreis"
        )

    return str(location) if location else "Kassel-Umkreis"


# ============================================================
# SUCHE
# ============================================================

def search_ads(query):
    params = {
        "q": query,
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "distance": DISTANCE_KM,
        "min_price": str(MIN_PRICE),
        "max_price": str(MAX_PRICE),
        "page": "0",
        "size": "100"
    }

    response = api("/search", params)

    data = response.get("data", {})
    ads = data.get("ads", [])

    if not isinstance(ads, list):
        raise RuntimeError(
            f"Unerwartetes Antwortformat bei Suche '{query}'."
        )

    print(f"Suche '{query}': {len(ads)} Inserate gefunden.")

    return ads


# ============================================================
# BEWERTUNG
# ============================================================

def evaluate_ads(ads):
    # Nur Angebote mit plausiblen Preisen behalten.
    filtered = {}

    for ad in ads:
        price = price_of(ad)

        if price is None:
            continue

        if not MIN_PRICE <= price <= MAX_PRICE:
            continue

        if not is_car(ad):
            continue

        ad_id = str(
            ad.get("ad_id")
            or ad.get("ad_url")
            or ad.get("id")
            or ""
        )

        if not ad_id:
            continue

        filtered[ad_id] = ad

    cars = list(filtered.values())

    # Vergleichsgruppen erstellen
    groups = {}

    for ad in cars:
        key = model_key(ad.get("title", ""))
        price = price_of(ad)

        groups.setdefault(key, []).append(price)

    results = []

    for ad in cars:
        price = price_of(ad)
        title = str(ad.get("title") or "Fahrzeugangebot")
        key = model_key(title)

        # Nur andere Angebote aus derselben groben Gruppe.
        comparisons = [
            value
            for value in groups.get(key, [])
            if price * 0.65 <= value <= price * 2.5
            and abs(value - price) > 100
        ]

        reserve = (
            defect_reserve(ad, price)
            + max(250, price * 0.08)
        )

        if len(comparisons) >= 2:
            comparisons.sort()
            market_value = comparisons[len(comparisons) // 2]

            estimated_profit = round(
                market_value - price - reserve
            )

            note = (
                f"{len(comparisons)} grobe Preisvergleiche. "
                "Baujahr, Kilometerstand und Ausstattung "
                "wurden nicht zuverlässig abgeglichen."
            )

        else:
            market_value = None
            estimated_profit = None

            note = (
                "Zu wenige Vergleichsangebote. "
                "Gewinn nicht zuverlässig schätzbar."
            )

        results.append({
            "id": str(
                ad.get("ad_id")
                or ad.get("id")
                or ""
            ),
            "title": title,
            "price": round(price),
            "estimated_market_value": (
                round(market_value)
                if market_value is not None
                else None
            ),
            "estimated_cost_reserve": round(reserve),
            "estimated_profit": estimated_profit,
            "target_profit_reached": (
                estimated_profit is not None
                and estimated_profit >= MIN_TARGET_PROFIT
            ),
            "location": get_location(ad),
            "created_at": ad.get("created_at"),
            "url": ad.get("ad_url") or "",
            "estimation_note": note
        })

    # Angebote mit berechenbarem Gewinn zuerst,
    # danach die günstigsten Angebote.
    results.sort(
        key=lambda item: (
            item["estimated_profit"] is None,
            -(
                item["estimated_profit"]
                if item["estimated_profit"] is not None
                else -999999
            ),
            item["price"]
        )
    )

    return results


# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():
    print("========================================")
    print("       AUTODEALSCANNER – KASSEL")
    print("========================================")
    print(f"Standort: {LATITUDE}, {LONGITUDE}")
    print(f"Suchradius: {DISTANCE_KM} km")
    print(f"Preisbereich: {MIN_PRICE}–{MAX_PRICE} Euro")
    print()

    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. Bitte GitHub Secret prüfen."
        )

    # Keine Standort-ID und keine Kategorie-ID notwendig.
    # Die Koordinaten sind fest hinterlegt.
    queries = (
        "Auto",
        "Gebrauchtwagen",
        "PKW"
    )

    all_ads = []
    errors = []

    for query in queries:
        try:
            all_ads.extend(search_ads(query))
        except Exception as error:
            message = f"{query}: {error}"
            errors.append(message)
            print("FEHLER:", message)

    if not all_ads and errors:
        raise RuntimeError(
            "Alle Suchabfragen sind fehlgeschlagen. "
            "Bitte API-Key, Guthaben und Fehlermeldungen prüfen. "
            + " | ".join(errors)
        )

    results = evaluate_ads(all_ads)

    profitable = [
        item for item in results
        if item["target_profit_reached"]
    ]

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "location": "Kassel",
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "distance_km": int(DISTANCE_KM),
        "minimum_price": MIN_PRICE,
        "maximum_price": MAX_PRICE,
        "minimum_target_profit": MIN_TARGET_PROFIT,
        "searches_run": len(queries),
        "ads_found": len(results),
        "potential_deals": len(profitable),
        "ads": results,
        "errors": errors,
        "notice": (
            "Gewinnschätzungen sind vorläufig. "
            "Inseratspreise allein belegen keinen erzielbaren "
            "Verkaufspreis. Fahrzeugzustand und Marktwert "
            "müssen vor einem Kauf geprüft werden."
        )
    }

    write_json(OUTPUT_FILE, output)

    write_json(STATE_FILE, {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "distance_km": DISTANCE_KM,
        "last_run": output["updated_at"]
    })

    print()
    print("========================================")
    print("SCAN ABGESCHLOSSEN")
    print("========================================")
    print(f"Ausgewertete Fahrzeuge: {len(results)}")
    print(f"Angebote mit geschätztem Gewinn ab 800 €: {len(profitable)}")
    print(f"Ergebnisdatei: {OUTPUT_FILE}")

    if errors:
        print()
        print("Einige Suchabfragen sind fehlgeschlagen:")
        for error in errors:
            print("-", error)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FEHLER: {error}", file=sys.stderr)
        sys.exit(1)
