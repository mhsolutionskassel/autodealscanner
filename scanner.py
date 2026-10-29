```python
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# --------------------------------------------------
# KONFIGURATION
# --------------------------------------------------

BASE = "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen"
KEY = os.environ.get("KLAZ_API_KEY", "").strip()

STATE = "data/state.json"
OUT = "data/results.json"

# Kassel: feste Koordinaten
LATITUDE = "51.3127"
LONGITUDE = "9.4797"

# Suchradius und Preisgrenzen
DISTANCE_KM = "100"
MIN_PRICE = "100"
MAX_PRICE = "7000"

SEARCH_TERMS = ("Auto", "Gebrauchtwagen", "PKW")


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
            "KLAZ_API_KEY fehlt. Bitte unter "
            "GitHub > Settings > Secrets and variables > "
            "Actions kontrollieren."
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
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"API-Fehler HTTP {e.code}: {detail}")

    except URLError as e:
        raise RuntimeError(f"API-Verbindung fehlgeschlagen: {e}")

    except json.JSONDecodeError:
        raise RuntimeError("Die API hat keine gültige JSON-Antwort geliefert.")

    if not isinstance(data, dict):
        raise RuntimeError("Unerwartetes Format der API-Antwort.")

    if data.get("success") is False:
        raise RuntimeError(
            data.get("message", "Die API-Suche ist fehlgeschlagen.")
        )

    return data


# --------------------------------------------------
# FAHRZEUGFILTER
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
        "autoanhänger",
        "autotransportanhänger",
        "anhänger",
        "felge",
        "reifen",
        "motoröl",
        "ersatzteil",
        "scheinwerfer",
        "getriebeöl",
        "suche auto",
        "autoteile",
    )

    brands_and_terms = (
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
        "škoda",
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
        "mini",
        "mitsubishi",
        "subaru",
        "smart",
        "fahrzeug",
        "gebrauchtwagen",
        "pkw",
        "auto ",
    )

    return (
        not any(word in text for word in excluded)
        and any(word in text for word in brands_and_terms)
    )


# --------------------------------------------------
# KOSTEN- UND RISIKORESERVE
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
        title.lower()
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
        "mini",
    )

    for index, word in enumerate(words):
        if word in makes:
            if index + 1 < len(words):
                return word + " " + words[index + 1]
            return word

    return " ".join(words[:2])


# --------------------------------------------------
# SUCHE UND BEWERTUNG
# --------------------------------------------------

def main():
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY ist nicht gesetzt. "
            "GitHub > Settings > Secrets and variables > Actions."
        )

    print("AutoDealScanner startet.")
    print("Standort: Kassel (feste Koordinaten)")
    print(f"Suchradius: {DISTANCE_KM} km")
    print(f"Preisbereich: {MIN_PRICE} bis {MAX_PRICE} Euro")

    previous = read(OUT, {})
    previous_ads = previous.get("ads", [])
    previous_ids = {
        str(ad.get("id"))
        for ad in previous_ads
        if ad.get("id")
    }

    unique = {}
    errors = []

    # Keine Standort- oder Kategorie-ID erforderlich.
    for query in SEARCH_TERMS:
        print(f"Suche: {query}")

        params = {
            "q": query,
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "distance": DISTANCE_KM,
            "min_price": MIN_PRICE,
            "max_price": MAX_PRICE,
            "ad_type": "OFFER",
            "page": "0",
            "size": "100",
        }

        try:
            response = api("/search", params)
            data = response.get("data", {})
            ads = data.get("ads", []) if isinstance(data, dict) else []

            print(f"  API-Treffer: {len(ads)}")

            for ad in ads or []:
                if not isinstance(ad, dict):
                    continue

                ad_id = str(
                    ad.get("ad_id")
                    or ad.get("ad_url")
                    or ""
                )

                if ad_id:
                    unique[ad_id] = ad

        except Exception as error:
            message = f"{query}: {error}"
            errors.append(message)
            print(f"  FEHLER: {message}")

    if not unique and errors:
        raise RuntimeError(
            "Alle Suchanfragen sind fehlgeschlagen: "
            + " | ".join(errors)
        )

    ads = []

    for ad in unique.values():
        price = price_of(ad)

        if price is None:
            continue

        if not (100 <= price <= 7000):
            continue

        if not carish(ad):
            continue

        ads.append(ad)

    print(f"Fahrzeugangebote nach Filter: {len(ads)}")

    # Preise nach groben Modellbezeichnungen gruppieren.
    groups = {}

    for ad in ads:
        key = key_for(str(ad.get("title", "")))
        groups.setdefault(key, []).append(price_of(ad))

    scored = []

    for ad in ads:
        price = price_of(ad)
        title = str(ad.get("title") or "Fahrzeugangebot")
        key = key_for(title)

        comparisons = [
            value
            for value in groups.get(key, [])
            if price * 0.65 <= value <= price * 2.5
            and abs(value - price) > 100
        ]

        repair_reserve = reserve(ad, price)
        additional_costs = max(250, price * 0.08)
        total_reserve = repair_reserve + additional_costs

        if len(comparisons) >= 2:
            comparisons.sort()
            market_value = comparisons[len(comparisons) // 2]

            estimated_profit = round(
                market_value - price - total_reserve
            )

            note = (
                f"Grobe Vergleichsgruppe mit "
                f"{len(comparisons)} Preisvergleichen. "
                "Modell, Baujahr, Laufleistung und Zustand "
                "wurden nicht vollständig abgeglichen."
            )
        else:
            market_value = price
            estimated_profit = -9999

            note = (
                "Zu wenige Vergleichsangebote. "
                "Ein möglicher Gewinn ist nicht zuverlässig "
                "schätzbar."
            )

        location = ad.get("location") or {}

        if isinstance(location, dict):
            location_name = (
                location.get("name")
                or location.get("city")
                or "Kassel-Umkreis"
            )
        else:
            location_name = str(location)

        ad_id = str(ad.get("ad_id") or ad.get("ad_url") or "")

        scored.append({
            "id": ad_id,
            "title": title,
            "price": round(price),
            "estimated_market_value": round(market_value),
            "cost_reserve": round(total_reserve),
            "estimated_profit": estimated_profit,
            "meets_profit_target": estimated_profit >= 800,
            "is_new": ad_id not in previous_ids,
            "estimation_note": note,
            "location": location_name,
            "created_at": ad.get("created_at"),
            "url": ad.get("ad_url") or "",
        })

    scored.sort(
        key=lambda item: (
            item["estimated_profit"] < 800,
            -item["estimated_profit"],
            item["price"],
        )
    )

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "location": "Kassel",
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "distance_km": int(DISTANCE_KM),
        "min_price": int(MIN_PRICE),
        "max_price": int(MAX_PRICE),
        "searches_run": len(SEARCH_TERMS),
        "total_found": len(scored),
        "new_ads": sum(1 for ad in scored if ad["is_new"]),
        "promising_ads": sum(
            1 for ad in scored
            if ad["estimated_profit"] >= 800
        ),
        "ads": scored,
        "errors": errors,
        "note": (
            "Gewinnschätzungen sind unverbindliche Vorbewertungen. "
            "Angebote werden nicht anhand vollständiger Fahrzeugdaten "
            "bewertet. Vor einem Kauf sind Zustand, Fahrzeughistorie, "
            "Reparaturkosten und realistische Verkaufspreise zu prüfen."
        ),
    }

    write(OUT, result)

    # Statusdatei für spätere Erweiterungen speichern.
    write(STATE, {
        "location": "Kassel",
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "updated_at": result["updated_at"],
    })

    print("")
    print("====================================")
    print("SCAN ABGESCHLOSSEN")
    print("====================================")
    print(f"Ausgewertete Fahrzeuge: {len(scored)}")
    print(f"Neue Inserate: {result['new_ads']}")
    print(f"Angebote mit geschätztem Gewinn >= 800 Euro: "
          f"{result['promising_ads']}")
    print(f"Ergebnisdatei: {OUT}")

    if errors:
        print("")
        print("Einzelne Suchanfragen hatten Fehler:")
        for error in errors:
            print(" - " + error)

    print("")
    print("Hinweis: Die Gewinnschätzung ist nur eine Vorauswahl.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FEHLER: {error}", file=sys.stderr)
        sys.exit(1)
```
