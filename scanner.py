import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen"
KEY = os.environ.get("KLAZ_API_KEY", "").strip()

STATE = "data/state.json"
OUT = "data/results.json"

LOCATION_NAME = "Kassel"
RADIUS_KM = 100
MIN_PRICE = 100
MAX_PRICE = 7000
MIN_TARGET_PROFIT = 800

# Wie viele Fahrzeuge pro Durchlauf mit ähnlichen Inseraten
# verglichen werden. Jede Ähnlichkeitssuche kostet laut API-Doku 1 Credit.
MAX_SIMILAR_LOOKUPS = max(
    0, int(os.environ.get("MAX_SIMILAR_LOOKUPS", "30"))
)

SEARCH_TERMS = ("Auto", "Gebrauchtwagen", "PKW")

MAKES = (
    "volkswagen", "vw", "opel", "ford", "bmw", "mercedes", "benz",
    "audi", "skoda", "seat", "renault", "peugeot", "citroen",
    "citroën", "dacia", "volvo", "fiat", "toyota", "hyundai",
    "kia", "mazda", "honda", "nissan", "suzuki", "mini",
    "mitsubishi", "subaru", "chevrolet", "alfa romeo", "smart",
    "porsche"
)

MODEL_TERMS = (
    "golf", "polo", "passat", "corsa", "astra", "focus", "fiesta",
    "clio", "megane", "twingo", "octavia", "fabia", "leon",
    "ibiza", "a1", "a3", "a4", "3er", "5er", "1er",
    "touran", "tiguan", "zafira", "meriva", "yaris", "corolla",
    "ceed", "cabrio", "limousine", "kombilimousine", "komb i"
)

EXCLUDE_WORDS = (
    "autoanhänger", "anhaenger", "anhänger", "felge", "reifen",
    "motoröl", "motoröl", "ersatzteil", "scheinwerfer",
    "getriebeöl", "suche auto", "schlachtfest", "nur teile",
    "autoteile", "teileträger"
)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def api(path, params=None):
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. Bitte GitHub → Settings → "
            "Secrets and variables → Actions prüfen."
        )

    url = BASE + path
    if params:
        url += "?" + urlencode(params)

    request = Request(
        url,
        headers={
            "klaz_key": KEY,
            "Accept": "application/json",
            "User-Agent": "AutoDealScanner/2.0"
        }
    )

    try:
        with urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))

    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(
            f"API HTTP {exc.code}: {body}"
        ) from exc

    except URLError as exc:
        raise RuntimeError(
            f"API-Verbindung fehlgeschlagen: {exc}"
        ) from exc

    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(
            payload.get("message", "API-Anfrage fehlgeschlagen")
        )

    return payload


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)

    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def find_location_id(payload):
    matches = []

    for item in walk(payload):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip().lower()
        city = str(item.get("city", "")).strip().lower()
        label = str(item.get("label", "")).strip().lower()

        ident = item.get("location_id", item.get("id"))
        if ident is None:
            continue

        if name == "kassel" or city == "kassel" or label == "kassel":
            matches.append((0, str(ident)))

        elif (
            name.startswith("kassel")
            or city.startswith("kassel")
            or label.startswith("kassel")
        ):
            matches.append((1, str(ident)))

    return sorted(matches)[0][1] if matches else None


def find_car_category_id(payload):
    found = []

    for item in walk(payload):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip().lower()
        ident = item.get("category_id", item.get("id"))

        if ident is not None and name in (
            "autos", "pkw", "autos & motorräder"
        ):
            priority = 0 if name == "autos" else 1
            found.append((priority, str(ident)))

    return sorted(found)[0][1] if found else None


def extract_ads(payload):
    if not isinstance(payload, dict):
        return []

    data = payload.get("data", {})

    if isinstance(data, dict) and isinstance(data.get("ads"), list):
        return data["ads"]

    if isinstance(payload.get("ads"), list):
        return payload["ads"]

    return []


def price_of(ad):
    value = ad.get("price")

    if isinstance(value, dict):
        value = value.get("amount")

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def ad_id_of(ad):
    return str(ad.get("ad_id") or ad.get("id") or "").strip()


def ad_url_of(ad):
    return str(ad.get("ad_url") or ad.get("url") or "").strip()


def seller_type_of(ad):
    seller = ad.get("seller") or {}

    if isinstance(seller, dict):
        seller_type = seller.get("type") or seller.get("account_type")
    else:
        seller_type = None

    seller_type = (
        seller_type
        or ad.get("poster_type")
        or ad.get("seller_type")
        or ""
    )

    return str(seller_type).strip().upper()


def is_private(ad):
    # Nur eindeutig als privat gekennzeichnete Inserate akzeptieren.
    return seller_type_of(ad) == "PRIVATE"


def is_car(ad):
    title = str(ad.get("title") or "").lower()
    description = str(ad.get("description") or "").lower()
    text = title + " " + description

    if any(word in text for word in EXCLUDE_WORDS):
        return False

    return any(make in text for make in MAKES) or any(
        model in text for model in MODEL_TERMS
    ) or any(word in text for word in (
        "gebrauchtwagen", "pkw", "kleinwagen", "fahrzeug"
    ))


def location_text(ad):
    loc = ad.get("location") or {}

    if isinstance(loc, dict):
        return str(
            loc.get("city")
            or loc.get("name")
            or LOCATION_NAME
        )

    return str(loc or LOCATION_NAME)


def reserve_for(ad, price):
    text = (
        str(ad.get("title") or "")
        + " "
        + str(ad.get("description") or "")
    ).lower()

    major_defects = (
        "motorschaden", "getriebeschaden", "unfallwagen",
        "bastlerfahrzeug", "nicht fahrbereit",
        "springt nicht an", "kupplung defekt"
    )

    moderate_defects = (
        "tüv abgelaufen", "tuev abgelaufen", "rost",
        "klima defekt", "kratzer", "delle", "reparatur",
        "defekt", "ölverlust", "oelverlust", "leuchtet"
    )

    if any(word in text for word in major_defects):
        return max(1500, price * 0.35)

    if any(word in text for word in moderate_defects):
        return max(900, price * 0.18)

    return max(600, price * 0.10)


def model_key(ad):
    """
    Einfache Vergleichsgruppe aus dem Inseratstitel.
    Das ersetzt keinen Abgleich von Baujahr, Motor oder Laufleistung.
    """
    title = str(ad.get("title") or "").lower()

    tokens = []
    for token in title.replace("-", " ").replace("/", " ").split():
        cleaned = "".join(char for char in token if char.isalnum())
        if cleaned:
            tokens.append(cleaned)

    make = next((m for m in MAKES if m in title), None)
    model = next((m for m in MODEL_TERMS if m in title), None)

    if make and model:
        return f"{make} {model}"

    if model:
        return model

    if make:
        return make

    return " ".join(tokens[:2])


def estimate_from_similar(ad, similar_ads):
    own_id = ad_id_of(ad)
    own_price = price_of(ad)
    own_key = model_key(ad)

    prices = []

    for other in similar_ads:
        if not isinstance(other, dict):
            continue

        if ad_id_of(other) == own_id:
            continue

        # Vergleichswerte ebenfalls auf private Inserate begrenzen.
        if not is_private(other):
            continue

        price = price_of(other)
        if price is None or not 100 <= price <= 50000:
            continue

        # Verhindert extrem weit entfernte Preisvergleiche.
        if own_price and not own_price * 0.55 <= price <= own_price * 2.5:
            continue

        other_key = model_key(other)

        # Wenn sich für beide Inserate eine Modellgruppe bestimmen lässt,
        # werden abweichende Gruppen nicht als Vergleich verwendet.
        if own_key != " " and other_key != " ":
            if own_key and other_key and own_key != other_key:
                continue

        prices.append(price)

    prices.sort()

    if len(prices) < 3:
        return None, len(prices), (
            "Zu wenige passende private Vergleichsangebote "
            "(mindestens 3 erforderlich)."
        )

    middle = len(prices) // 2

    if len(prices) % 2:
        market = prices[middle]
    else:
        market = (prices[middle - 1] + prices[middle]) / 2

    return round(market), len(prices), (
        f"Median aus {len(prices)} ähnlichen privaten Inseraten. "
        "Baujahr, Kilometerstand, Motorisierung und Zustand "
        "vor dem Kauf unbedingt vergleichen."
    )


def main():
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY ist nicht gesetzt. GitHub → Settings → "
            "Secrets and variables → Actions prüfen."
        )

    state = read_json(STATE, {})
    location_id = state.get("location_id")
    category_id = state.get("category_id")

    if not location_id:
        location_payload = api(
            "/locations",
            {"q": LOCATION_NAME, "limit": 100}
        )
        location_id = find_location_id(location_payload)

        if not location_id:
            raise RuntimeError(
                "Kassel wurde in der Standortantwort nicht gefunden. "
                "Es wird kein anderer Ort verwendet."
            )

    if not category_id:
        category_payload = api("/categories")
        category_id = find_car_category_id(category_payload)

        if not category_id:
            raise RuntimeError(
                "Auto-Kategorie-ID konnte nicht ermittelt werden."
            )

    state.update({
        "location_id": str(location_id),
        "category_id": str(category_id),
        "location_name": LOCATION_NAME,
        "radius_km": RADIUS_KM
    })

    unique = {}
    search_errors = []

    for query in SEARCH_TERMS:
        try:
            response = api("/search", {
                "q": query,
                "location_id": str(location_id),
                "distance": str(RADIUS_KM),
                "min_price": str(MIN_PRICE),
                "max_price": str(MAX_PRICE),
                "category_id": str(category_id),
                "ad_type": "OFFER",
                "poster_type": "PRIVATE",
                "page": "0",
                "size": "100"
            })

            for ad in extract_ads(response):
                if not isinstance(ad, dict):
                    continue

                # Doppelte Anzeigen zusammenführen.
                ident = ad_id_of(ad) or ad_url_of(ad)
                if ident:
                    unique[ident] = ad

        except Exception as exc:
            search_errors.append(f"{query}: {exc}")

    if not unique and search_errors:
        raise RuntimeError(
            "Alle Suchanfragen fehlgeschlagen: "
            + " | ".join(search_errors)
        )

    # Sicherheitsfilter: Auch nach der API-Suche ausschließlich privat.
    candidates = [
        ad for ad in unique.values()
        if is_private(ad)
        and price_of(ad) is not None
        and MIN_PRICE <= price_of(ad) <= MAX_PRICE
        and is_car(ad)
    ]

    candidates.sort(
        key=lambda ad: (
            str(ad.get("created_at") or "9999"),
            price_of(ad) or 999999
        )
    )

    similar_results = {}
    similar_errors = {}

    for ad in candidates[:MAX_SIMILAR_LOOKUPS]:
        ident = ad_id_of(ad)

        if not ident:
            continue

        try:
            response = api(
                f"/ads/{ident}/similar",
                {"size": "10"}
            )
            similar_results[ident] = extract_ads(response)

        except Exception as exc:
            similar_errors[ident] = str(exc)

    scored = []

    for ad in candidates:
        ident = ad_id_of(ad)
        price = price_of(ad)

        market, comp_count, note = estimate_from_similar(
            ad,
            similar_results.get(ident, [])
        )

        # Rücklage für Reparatur/Aufbereitung plus weitere Nebenkosten.
        repair_reserve = reserve_for(ad, price)
        other_costs = max(250, price * 0.08)
        cost_reserve = round(repair_reserve + other_costs)

        if market is not None:
            profit = round(market - price - cost_reserve)

            if profit >= MIN_TARGET_PROFIT:
                status = "PRÜFEN – mögliches Angebot"
            elif profit >= 0:
                status = "Möglicher kleiner Gewinn"
            else:
                status = "Kein rechnerischer Gewinn"

        else:
            profit = None
            status = "NICHT AUSREICHEND BEWERTBAR"

            if ident in similar_errors:
                note = (
                    "Ähnlichkeitssuche fehlgeschlagen: "
                    + similar_errors[ident]
                )
            elif ident not in similar_results:
                note = (
                    "Für dieses Fahrzeug wurde keine Ähnlichkeitssuche "
                    f"ausgeführt (Limit: {MAX_SIMILAR_LOOKUPS})."
                )

        scored.append({
            "id": ident,
            "title": str(ad.get("title") or "Fahrzeugangebot"),
            "price": round(price),
            "estimated_market_value": market,
            "cost_reserve": cost_reserve,
            "estimated_profit": profit,
            "profit_target": MIN_TARGET_PROFIT,
            "status": status,
            "seller_type": seller_type_of(ad),
            "comparable_ads": comp_count,
            "estimation_note": note,
            "location": location_text(ad),
            "created_at": ad.get("created_at"),
            "url": ad_url_of(ad)
        })

    # Höchster geschätzter Gewinn zuerst.
    # Angebote ohne ausreichende Vergleichsdaten kommen ans Ende.
    scored.sort(key=lambda item: (
        item["estimated_profit"] is None,
        -(item["estimated_profit"] or -999999),
        item["price"]
    ))

    profitable = sum(
        1 for item in scored
        if item.get("estimated_profit") is not None
        and item["estimated_profit"] >= MIN_TARGET_PROFIT
    )

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "location": LOCATION_NAME,
        "radius_km": RADIUS_KM,
        "min_price": MIN_PRICE,
        "max_price": MAX_PRICE,
        "seller_filter": "PRIVATE",
        "searches_run": len(SEARCH_TERMS),
        "ads_found": len(unique),
        "cars_evaluated": len(candidates),
        "similar_checks_run": len(similar_results),
        "similar_checks_limit": MAX_SIMILAR_LOOKUPS,
        "min_target_profit": MIN_TARGET_PROFIT,
        "profitable_offers": profitable,
        "ads": scored,
        "errors": search_errors,
        "note": (
            "Marktwerte sind unverbindliche Schätzungen aus ähnlichen "
            "privaten Live-Inseraten. Die Ähnlichkeitssuche liefert "
            "höchstens 10 Treffer. Baujahr, Kilometerstand, Motorisierung "
            "und Zustand werden nicht zwingend exakt abgeglichen. "
            "Kosten und Gewinn müssen vor einem Kauf selbst geprüft werden."
        )
    }

    write_json(OUT, result)
    write_json(STATE, state)

    print(
        f"Fertig: {len(candidates)} private Fahrzeuge; "
        f"{len(similar_results)} Ähnlichkeitsabfragen; "
        f"{profitable} Angebote mit geschätztem Gewinn >= "
        f"{MIN_TARGET_PROFIT} EUR."
    )

    if search_errors:
        print("Suchfehler: " + " | ".join(search_errors))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FEHLER:", exc, file=sys.stderr)
        sys.exit(1)
```
