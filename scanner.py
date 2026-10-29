import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen"
KEY = os.environ.get("KLAZ_API_KEY", "").strip()

STATE_FILE = "data/state.json"
OUT_FILE = "data/results.json"

LOCATION_NAME = "Kassel"
DISTANCE_KM = 100
MIN_PRICE = 100
MAX_PRICE = 7000
MIN_PROFIT = 800

SEARCH_TERMS = ("Auto", "Gebrauchtwagen", "PKW")

BRANDS = (
    "volkswagen", "vw", "opel", "ford", "bmw", "mercedes",
    "benz", "audi", "skoda", "seat", "renault", "peugeot",
    "citroen", "citroën", "dacia", "volvo", "fiat", "toyota",
    "hyundai", "kia", "mazda", "honda", "nissan", "suzuki",
    "mitsubishi", "mini", "smart", "alfa romeo", "chevrolet"
)

EXCLUDE_WORDS = (
    "autoanhänger", "anhänger", "felge", "reifen", "motoröl",
    "ersatzteil", "scheinwerfer", "getriebeöl", "suche auto",
    "motorrad", "roller", "quad", "wohnmobil", "wohnwagen"
)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def api(path, params=None):
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. Bitte in GitHub Secrets hinterlegen."
        )

    url = BASE + path
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
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(
            f"API-Fehler HTTP {error.code}: {details}"
        )
    except URLError as error:
        raise RuntimeError(f"API-Verbindung fehlgeschlagen: {error}")

    if data.get("success") is False:
        raise RuntimeError(data.get("message", "API-Suche fehlgeschlagen"))

    return data


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def find_location_id(payload):
    candidates = []

    for item in walk(payload):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip().lower()
        city = str(item.get("city", "")).strip().lower()
        label = str(item.get("label", "")).strip().lower()

        identifier = item.get("location_id") or item.get("id")

        if identifier is None:
            continue

        if (
            name == "kassel"
            or city == "kassel"
            or label == "kassel"
            or name.startswith("kassel,")
            or city.startswith("kassel,")
            or label.startswith("kassel,")
        ):
            priority = 0 if (
                name == "kassel" or city == "kassel"
            ) else 1
            candidates.append((priority, str(identifier)))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][1]


def find_auto_category_id(payload):
    candidates = []

    for item in walk(payload):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip().lower()
        identifier = item.get("category_id", item.get("id"))

        if identifier is not None and name in ("autos", "pkw"):
            priority = 0 if name == "autos" else 1
            candidates.append((priority, str(identifier)))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][1]


def get_price(ad):
    value = ad.get("price")

    if isinstance(value, dict):
        value = value.get("amount")

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def get_seller_type(ad):
    seller = ad.get("seller")

    if isinstance(seller, dict):
        value = seller.get("type") or seller.get("account_type")
        if value:
            return str(value).strip().upper()

    value = ad.get("poster_type") or ad.get("seller_type")
    if value:
        return str(value).strip().upper()

    return ""


def is_car(ad):
    title = str(ad.get("title", ""))
    description = str(ad.get("description", ""))
    text = (title + " " + description).lower()

    if any(word in text for word in EXCLUDE_WORDS):
        return False

    return any(brand in text for brand in BRANDS)


def estimate_reserve(ad, price):
    text = (
        str(ad.get("title", "")) + " " +
        str(ad.get("description", "")
    )).lower()

    severe = (
        "motorschaden", "getriebeschaden", "unfallwagen",
        "bastlerfahrzeug", "nicht fahrbereit", "springt nicht an",
        "kupplung defekt"
    )

    moderate = (
        "tüv abgelaufen", "tuev abgelaufen", "rost",
        "klima defekt", "kratzer", "delle", "reparatur",
        "defekt", "ölverlust", "oelverlust"
    )

    if any(word in text for word in severe):
        return round(max(1500, price * 0.35))

    if any(word in text for word in moderate):
        return round(max(900, price * 0.18))

    return round(max(600, price * 0.10))


def model_key(title):
    words = re.sub(
        r"[^a-z0-9äöüß ]", " ", str(title).lower()
    ).split()

    for index, word in enumerate(words):
        if word in BRANDS:
            next_word = words[index + 1] if index + 1 < len(words) else ""
            return (word + " " + next_word).strip()

    return " ".join(words[:2])


def listing_id(ad):
    return str(ad.get("ad_id") or ad.get("id") or ad.get("ad_url") or "")


def listing_url(ad):
    return str(ad.get("ad_url") or ad.get("url") or "")


def listing_location(ad):
    location = ad.get("location") or {}

    if isinstance(location, dict):
        return (
            location.get("name")
            or location.get("city")
            or "Kassel-Umkreis"
        )

    return str(location) if location else "Kassel-Umkreis"


def search_ads(location_id, category_id):
    unique = {}
    errors = []

    for term in SEARCH_TERMS:
        params = {
            "q": term,
            "location_id": str(location_id),
            "distance": str(DISTANCE_KM),
            "min_price": str(MIN_PRICE),
            "max_price": str(MAX_PRICE),
            "category_id": str(category_id),
            "ad_type": "OFFER",
            "poster_type": "PRIVATE",
            "page": "0",
            "size": "100"
        }

        try:
            response = api("/search", params)
            data = response.get("data", {})
            ads = data.get("ads", []) or []

            for ad in ads:
                identifier = listing_id(ad)
                if identifier:
                    unique[identifier] = ad

            print(
                f"Suche '{term}': {len(ads)} Treffer "
                "(Filter: Privatpersonen)"
            )

        except Exception as error:
            message = f"{term}: {error}"
            errors.append(message)
            print("WARNUNG:", message)

    if not unique and errors:
        raise RuntimeError(
            "Alle Suchanfragen sind fehlgeschlagen: "
            + " | ".join(errors)
        )

    return list(unique.values()), errors


def evaluate_ads(ads):
    # Sicherheitsfilter: Gewerbliche Anbieter und Inserate
    # ohne erkennbaren Anbietertyp werden nicht aufgenommen.
    private_ads = []

    for ad in ads:
        seller_type = get_seller_type(ad)

        if seller_type != "PRIVATE":
            continue

        price = get_price(ad)

        if price is None or not (MIN_PRICE <= price <= MAX_PRICE):
            continue

        if not is_car(ad):
            continue

        private_ads.append(ad)

    # Vergleichsgruppen anhand des im Titel erkannten Modells.
    groups = {}

    for ad in private_ads:
        price = get_price(ad)
        key = model_key(ad.get("title", ""))

        groups.setdefault(key, []).append(price)

    results = []

    for ad in private_ads:
        price = get_price(ad)
        key = model_key(ad.get("title", ""))

        comparable = [
            value for value in groups.get(key, [])
            if price * 0.65 <= value <= price * 2.5
            and abs(value - price) > 100
        ]

        comparable.sort()

        reserve = estimate_reserve(ad, price)
        additional_costs = round(max(250, price * 0.08))

        if len(comparable) >= 2:
            market_value = comparable[len(comparable) // 2]
            profit = round(
                market_value - price - reserve - additional_costs
            )
            note = (
                f"{len(comparable)} ähnliche private Angebote. "
                "Modell, Baujahr, Kilometerstand und Zustand "
                "vor dem Kauf genau vergleichen."
            )
            status = (
                "PRÜFEN – mögliches Angebot"
                if profit >= MIN_PROFIT
                else "Unter Mindestgewinn"
            )
        else:
            market_value = None
            profit = None
            note = (
                "Zu wenige vergleichbare private Angebote. "
                "Ein belastbarer Marktwert ist nicht ermittelbar."
            )
            status = "Marktwert offen"

        results.append({
            "id": listing_id(ad),
            "title": str(ad.get("title") or "Fahrzeugangebot"),
            "price": round(price),
            "estimated_market_value": (
                round(market_value) if market_value is not None else None
            ),
            "cost_reserve": round(reserve + additional_costs),
            "estimated_profit": profit,
            "profit_target": (
                profit is not None and profit >= MIN_PROFIT
            ),
            "status": status,
            "seller_type": "PRIVATE",
            "comparable_ads": len(comparable),
            "estimation_note": note,
            "location": listing_location(ad),
            "created_at": ad.get("created_at"),
            "url": listing_url(ad)
        })

    results.sort(
        key=lambda item: (
            item["estimated_profit"] is None,
            -(item["estimated_profit"] or -999999),
            item["price"]
        )
    )

    return results


def main():
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. Bitte unter "
            "GitHub → Settings → Secrets and variables → "
            "Actions als Repository Secret hinterlegen."
        )

    state = read_json(STATE_FILE, {})
    location_id = state.get("location_id")
    category_id = state.get("category_id")
    discovery_count = 0

    if not location_id:
        response = api("/locations", {"q": LOCATION_NAME, "limit": 100})
        location_id = find_location_id(response)
        discovery_count += 1

        if not location_id:
            raise RuntimeError(
                "Kassel wurde bei der Standortsuche nicht gefunden. "
                "Bitte Standortantwort der API prüfen."
            )

    if not category_id:
        response = api("/categories")
        category_id = find_auto_category_id(response)
        discovery_count += 1

        if not category_id:
            raise RuntimeError(
                "Die Kategorie Autos/Pkw wurde nicht erkannt."
            )

    state.update({
        "location_id": str(location_id),
        "category_id": str(category_id)
    })

    ads, errors = search_ads(location_id, category_id)
    results = evaluate_ads(ads)

    target_count = sum(
        1 for item in results
        if item["estimated_profit"] is not None
        and item["estimated_profit"] >= MIN_PROFIT
    )

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "location": LOCATION_NAME,
        "radius_km": DISTANCE_KM,
        "min_price": MIN_PRICE,
        "max_price": MAX_PRICE,
        "seller_filter": "PRIVATE",
        "min_target_profit": MIN_PROFIT,
        "searches_run": len(SEARCH_TERMS) + discovery_count,
        "ads_found": len(ads),
        "cars_evaluated": len(results),
        "similar_checks_run": len(results),
        "similar_checks_limit": len(results),
        "target_count": target_count,
        "ads": results,
        "errors": errors,
        "note": (
            "Es werden ausschließlich Inserate berücksichtigt, "
            "deren Anbietertyp als PRIVATE erkannt wurde. "
            "Gewinnschätzungen sind unverbindlich. "
            "Vor einem Kauf Fahrzeugzustand, Marktwert und "
            "Reparaturkosten selbst prüfen."
        )
    }

    write_json(OUT_FILE, output)
    write_json(STATE_FILE, state)

    print("")
    print("SCAN ERFOLGREICH")
    print(f"Standort: {LOCATION_NAME}")
    print(f"Umkreis: {DISTANCE_KM} km")
    print("Anbietertyp: ausschließlich Privatpersonen")
    print(f"Suchtreffer insgesamt: {len(ads)}")
    print(f"Private Fahrzeuge ausgewertet: {len(results)}")
    print(f"Kandidaten ab {MIN_PROFIT} € geschätztem Gewinn: {target_count}")
    print(f"Ergebnisdatei: {OUT_FILE}")

    if errors:
        print("Einzelne Suchfehler:")
        for error in errors:
            print("-", error)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("FEHLER:", error, file=sys.stderr)
        sys.exit(1)
