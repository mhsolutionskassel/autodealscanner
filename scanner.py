import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# --------------------------------------------------
# KONFIGURATION: KASSEL FEST HINTERLEGT
# --------------------------------------------------

BASE = "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen"
KEY = os.environ.get("KLAZ_API_KEY", "").strip()

LATITUDE = "51.3127"
LONGITUDE = "9.4797"
DISTANCE_KM = "100"

MIN_PRICE = 100
MAX_PRICE = 7000
MIN_TARGET_PROFIT = 800

STATE = "data/state.json"
OUT = "data/results.json"


# --------------------------------------------------
# DATEIEN
# --------------------------------------------------

def read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# --------------------------------------------------
# API
# --------------------------------------------------

def api(path, params=None):
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. "
            "GitHub > Settings > Secrets and variables > Actions"
        )

    url = BASE + path

    if params:
        url += "?" + urlencode(params)

    req = Request(
        url,
        headers={
            "klaz_key": KEY,
            "Accept": "application/json",
            "User-Agent": "AutoDealScanner/1.0",
        },
    )

    try:
        with urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))

    except HTTPError as e:
        details = e.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(
            f"API-Fehler HTTP {e.code}: {details}"
        )

    except URLError as e:
        raise RuntimeError(
            f"Verbindung zur API fehlgeschlagen: {e}"
        )

    if data.get("success") is False:
        raise RuntimeError(
            data.get("message", "API-Suche fehlgeschlagen")
        )

    return data


# --------------------------------------------------
# FAHRZEUGERKENNUNG
# --------------------------------------------------

def price_of(ad):
    price = ad.get("price")

    if isinstance(price, dict):
        price = price.get("amount")

    try:
        return float(price)
    except (ValueError, TypeError):
        return None


def carish(ad):
    text = (
        str(ad.get("title", ""))
        + " "
        + str(ad.get("description", ""))
    ).lower()

    excluded = (
        "anhänger",
        "autoanhänger",
        "felge",
        "reifen",
        "motoröl",
        "ersatzteil",
        "scheinwerfer",
        "getriebeöl",
        "suche auto",
        "nur ersatzteile",
    )

    brands = (
        "volkswagen",
        "vw ",
        "golf",
        "polo",
        "passat",
        "opel",
        "astra",
        "corsa",
        "ford",
        "focus",
        "fiesta",
        "bmw",
        "mercedes",
        "benz",
        "audi",
        "skoda",
        "seat",
        "renault",
        "peugeot",
        "citroën",
        "citroen",
        "dacia",
        "volvo",
        "fiat",
        "toyota",
        "hyundai",
        "kia",
        "mazda",
        "honda",
        "nissan",
        "suzuki",
        "mitsubishi",
        "mini",
        "smart",
        "gebrauchtwagen",
        "pkw",
        "auto ",
    )

    return (
        not any(word in text for word in excluded)
        and any(word in text for word in brands)
    )


# --------------------------------------------------
# REPARATUR- UND RISIKORESERVE
# --------------------------------------------------

def reserve(ad, price):
    text = (
        str(ad.get("title", ""))
        + " "
        + str(ad.get("description", ""))
    ).lower()

    major_defects = (
        "motorschaden",
        "getriebeschaden",
        "unfallwagen",
        "bastlerfahrzeug",
        "nicht fahrbereit",
        "springt nicht an",
        "kupplung defekt",
    )

    minor_defects = (
        "tüv abgelaufen",
        "tuev abgelaufen",
        "rost",
        "klima defekt",
        "kratzer",
        "delle",
        "reparatur",
        "defekt",
        "ölverlust",
        "oelverlust",
    )

    if any(word in text for word in major_defects):
        return max(1500, price * 0.35)

    if any(word in text for word in minor_defects):
        return max(900, price * 0.18)

    return max(600, price * 0.10)


# --------------------------------------------------
# VERGLEICHSGRUPPEN
# --------------------------------------------------

def key_for(title):
    words = re.sub(
        r"[^a-z0-9äöüß ]",
        " ",
        title.lower(),
    ).split()

    makes = (
        "volkswagen",
        "vw",
        "opel",
        "ford",
        "bmw",
        "mercedes",
        "benz",
        "audi",
        "skoda",
        "seat",
        "renault",
        "peugeot",
        "citroen",
        "citroën",
        "dacia",
        "volvo",
        "fiat",
        "toyota",
        "hyundai",
        "kia",
        "mazda",
        "honda",
        "nissan",
        "suzuki",
        "mitsubishi",
        "mini",
        "smart",
    )

    for index, word in enumerate(words):
        if word in makes:
            if index + 1 < len(words):
                return word + " " + words[index + 1]
            return word

    return " ".join(words[:2])


# --------------------------------------------------
# SUCHE
# --------------------------------------------------

def main():
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. "
            "Bitte GitHub Secrets kontrollieren."
        )

    print("AutoDealScanner startet.")
    print("Standort: Kassel")
    print(f"Koordinaten: {LATITUDE}, {LONGITUDE}")
    print(f"Suchradius: {DISTANCE_KM} km")
    print(f"Preisbereich: {MIN_PRICE} bis {MAX_PRICE} EUR")

    queries = (
        "Auto",
        "Gebrauchtwagen",
        "PKW",
    )

    unique = {}
    errors = []

    for query in queries:
        print(f"Suche läuft: {query}")

        params = {
            "q": query,
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "distance": DISTANCE_KM,
            "min_price": str(MIN_PRICE),
            "max_price": str(MAX_PRICE),
            "page": "0",
            "size": "100",
        }

        try:
            result = api("/search", params)

            data = result.get("data", {})
            ads = data.get("ads", [])

            print(f"  API-Treffer: {len(ads)}")

            for ad in ads:
                ad_id = (
                    ad.get("ad_id")
                    or ad.get("ad_url")
                )

                if ad_id:
                    unique[str(ad_id)] = ad

        except Exception as error:
            message = f"{query}: {error}"
            errors.append(message)
            print(f"  FEHLER: {message}")

    if not unique and errors:
        raise RuntimeError(
            "Keine Suche erfolgreich. API-Antworten: "
            + " | ".join(errors)
        )

    ads = []

    for ad in unique.values():
        price = price_of(ad)

        if price is None:
            continue

        if not MIN_PRICE <= price <= MAX_PRICE:
            continue

        if not carish(ad):
            continue

        ads.append(ad)

    print(f"Erkannte Fahrzeuge: {len(ads)}")

    # Vergleichsangebote nach grobem Modell-Schlüssel gruppieren
    groups = {}

    for ad in ads:
        title = str(ad.get("title", ""))
        price = price_of(ad)
        key = key_for(title)

        groups.setdefault(key, []).append(price)

    scored = []

    for ad in ads:
        price = price_of(ad)
        title = str(ad.get("title", "Fahrzeugangebot"))
        key = key_for(title)

        comparisons = [
            value
            for value in groups.get(key, [])
            if price * 0.65 <= value <= price * 2.5
            and abs(value - price) > 100
        ]

        if len(comparisons) >= 2:
            comparisons.sort()
            market_value = comparisons[len(comparisons) // 2]

            repair_reserve = reserve(ad, price)
            additional_costs = max(250, price * 0.08)

            estimated_profit = round(
                market_value
                - price
                - repair_reserve
                - additional_costs
            )

            note = (
                f"{len(comparisons)} grobe Vergleichsangebote. "
                "Modell, Baujahr und Laufleistung nicht vollständig "
                "abgeglichen."
            )

        else:
            market_value = price
            repair_reserve = reserve(ad, price)
            additional_costs = max(250, price * 0.08)

            estimated_profit = -9999

            note = (
                "Zu wenige Vergleichsangebote. "
                "Kein belastbarer Gewinn ermittelbar."
            )

        location = ad.get("location") or {}

        scored.append({
            "id": str(ad.get("ad_id", "")),
            "title": title,
            "price": round(price),
            "estimated_market_value": round(market_value),
            "cost_reserve": round(
                repair_reserve + additional_costs
            ),
            "estimated_profit": estimated_profit,
            "target_profit_reached": (
                estimated_profit >= MIN_TARGET_PROFIT
            ),
            "estimation_note": note,
            "location": (
                location.get("name")
                or location.get("city")
                or "Kassel-Umkreis"
            ),
            "created_at": ad.get("created_at"),
            "url": ad.get("ad_url") or "",
        })

    # Attraktivste Angebote zuerst
    scored.sort(
        key=lambda item: (
            item["estimated_profit"] < MIN_TARGET_PROFIT,
            -item["estimated_profit"],
            item["price"],
        )
    )

    result_file = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "location": "Kassel",
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "distance_km": int(DISTANCE_KM),
        "searches_run": len(queries),
        "total_unique_ads": len(unique),
        "vehicles_evaluated": len(scored),
        "minimum_target_profit": MIN_TARGET_PROFIT,
        "ads": scored,
        "note": (
            "Gewinnschätzungen sind unverbindlich. "
            "Vergleichspreise sind keine garantierten Verkaufspreise. "
            "Steuern und laufende Fixkosten sind nicht enthalten."
        ),
        "errors": errors,
    }

    write(OUT, result_file)

    state = read(STATE, {})
    state.update({
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "distance_km": int(DISTANCE_KM),
    })
    write(STATE, state)

    attractive = [
        ad for ad in scored
        if ad["estimated_profit"] >= MIN_TARGET_PROFIT
    ]

    print("")
    print("========== ERGEBNIS ==========")
    print(f"Einmalige Inserate: {len(unique)}")
    print(f"Ausgewertete Fahrzeuge: {len(scored)}")
    print(f"Angebote mit Zielgewinn >= 800 EUR: {len(attractive)}")
    print(f"Ergebnisdatei: {OUT}")

    if errors:
        print("")
        print("Einzelne Suchfehler:")
        for error in errors:
            print(" - " + error)

    print("==============================")

    if not scored and errors:
        raise RuntimeError(
            "Es konnten keine Fahrzeuge ausgewertet werden."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FEHLER: {error}", file=sys.stderr)
        sys.exit(1)
