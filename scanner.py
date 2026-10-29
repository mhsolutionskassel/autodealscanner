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
MAX_SIMILAR_LOOKUPS = max(
    0, int(os.environ.get("MAX_SIMILAR_LOOKUPS", "30"))
)

MAKES = (
    "volkswagen", "vw", "opel", "ford", "bmw", "mercedes", "benz", "audi",
    "skoda", "seat", "renault", "peugeot", "citroen", "citroën", "dacia",
    "volvo", "fiat", "toyota", "hyundai", "kia", "mazda", "honda", "nissan",
    "suzuki", "mini", "mitsubishi", "subaru", "chevrolet", "alfa romeo",
    "smart", "porsche"
)

EXCLUDE_WORDS = (
    "autoanhänger", "anhänger", "felge", "reifen", "motoröl", "ersatzteil",
    "scheinwerfer", "getriebeöl", "suche auto", "schlachtfest", "nur teile"
)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def api(path, params=None):
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. GitHub → Settings → Secrets and variables "
            "→ Actions prüfen."
        )

    url = BASE + path

    if params:
        url += "?" + urlencode(params)

    req = Request(
        url,
        headers={
            "klaz_key": KEY,
            "Accept": "application/json",
            "User-Agent": "AutoDealScanner/1.0"
        }
    )

    try:
        with urlopen(req, timeout=45) as response:
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

        if (
            name == "kassel"
            or city == "kassel"
            or label == "kassel"
        ):
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
            "autos",
            "pkw",
            "autos & motorräder"
        ):
            found.append(
                (0 if name == "autos" else 1, str(ident))
            )

    return sorted(found)[0][1] if found else None


def price_of(ad):
    value = ad.get("price")

    if isinstance(value, dict):
        value = value.get("amount")

    try:
        return float(value)

    except (ValueError, TypeError):
        return None


def ad_id_of(ad):
    return str(
        ad.get("ad_id") or ad.get("id") or ""
    ).strip()


def ad_url_of(ad):
    return str(
        ad.get("ad_url") or ad.get("url") or ""
    ).strip()


def is_car(ad):
    text = (
        str(ad.get("title") or "")
        + " "
        + str(ad.get("description") or "")
    ).lower()

    if any(word in text for word in EXCLUDE_WORDS):
        return False

    terms = MAKES + (
        "golf", "polo", "passat", "corsa", "astra", "focus", "fiesta",
        "clio", "megane", "twingo", "octavia", "fabia", "leon", "ibiza",
        "a1", "a3", "a4", "3er", "5er", "1er", "gebrauchtwagen", "pkw",
        "cabrio", "limousine", "touran", "tiguan", "zafira", "meriva",
        "yaris", "corolla", "ceed", "freemont"
    )

    return any(term in text for term in terms)


def reserve_for(ad, price):
    text = (
        str(ad.get("title") or "")
        + " "
        + str(ad.get("description") or "")
    ).lower()

    if any(word in text for word in (
        "motorschaden",
        "getriebeschaden",
        "unfallwagen",
        "bastlerfahrzeug",
        "nicht fahrbereit",
        "springt nicht an",
        "kupplung defekt"
    )):
        return max(1500, price * 0.35)

    if any(word in text for word in (
        "tüv abgelaufen",
        "tuev abgelaufen",
        "rost",
        "klima defekt",
        "kratzer",
        "delle",
        "reparatur",
        "defekt",
        "ölverlust",
        "oelverlust"
    )):
        return max(900, price * 0.18)

    return max(600, price * 0.10)


def location_text(ad):
    loc = ad.get("location") or {}

    if isinstance(loc, dict):
        return str(
            loc.get("name")
            or loc.get("city")
            or LOCATION_NAME
        )

    return str(loc or LOCATION_NAME)


def extract_ads(payload):
    if isinstance(payload, dict):
        data = payload.get("data", {})

        if (
            isinstance(data, dict)
            and isinstance(data.get("ads"), list)
        ):
            return data["ads"]

        if isinstance(payload.get("ads"), list):
            return payload["ads"]

    return []


def estimate_from_similar(ad, similar_ads):
    own_id = ad_id_of(ad)
    own_price = price_of(ad)
    prices = []

    for other in similar_ads:
        if not isinstance(other, dict):
            continue

        if ad_id_of(other) == own_id:
            continue

        price = price_of(other)

        if price is None or not 100 <= price <= 50000:
            continue

        if own_price and not (
            own_price * 0.55 <= price <= own_price * 2.5
        ):
            continue

        prices.append(price)

    prices.sort()

    if len(prices) < 3:
        return (
            None,
            len(prices),
            "Weniger als 3 brauchbare ähnliche Preisvergleiche; "
            "Gewinn nicht belastbar schätzbar."
        )

    mid = len(prices) // 2

    if len(prices) % 2:
        market = prices[mid]
    else:
        market = (prices[mid - 1] + prices[mid]) / 2

    return (
        round(market),
        len(prices),
        f"Median aus {len(prices)} ähnlichen Inseraten. "
        "Baujahr, Laufleistung und Zustand bitte manuell prüfen."
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
        location_id = find_location_id(
            api("/locations", {
                "q": LOCATION_NAME,
                "limit": 100
            })
        )

        if not location_id:
            raise RuntimeError(
                "Kassel wurde nicht in der Standortantwort gefunden. "
                "Es wird kein anderer Ort verwendet."
            )

    if not category_id:
        category_id = find_car_category_id(
            api("/categories")
        )

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

    queries = ("Auto", "Gebrauchtwagen", "PKW")

    for query in queries:
        try:
            response = api("/search", {
                "q": query,
                "location_id": str(location_id),
                "distance": str(RADIUS_KM),
                "min_price": str(MIN_PRICE),
                "max_price": str(MAX_PRICE),
                "category_id": str(category_id),
                "ad_type": "OFFER",
                "page": "0",
                "size": "100"
            })

            for ad in extract_ads(response):
                if not isinstance(ad, dict):
                    continue

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

    candidates = [
        ad for ad in unique.values()
        if price_of(ad) is not None
        and MIN_PRICE <= price_of(ad) <= MAX_PRICE
        and is_car(ad)
    ]

    candidates.sort(key=lambda ad: (
        str(ad.get("created_at") or "9999"),
        price_of(ad) or 999999
    ))

    similar_results = {}
    similar_errors = {}

    for ad in candidates[:MAX_SIMILAR_LOOKUPS]:
        ident = ad_id_of(ad)

        if not ident:
            continue

        try:
            similar_results[ident] = extract_ads(
                api(
                    f"/ads/{ident}/similar",
                    {"size": "10"}
                )
            )

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

        cost_reserve = round(
            reserve_for(ad, price)
            + max(250, price * 0.08)
        )

        if market is not None:
            profit = round(
                market - price - cost_reserve
            )

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
                    "Für dieses Fahrzeug wurde in diesem Lauf keine "
                    "Ähnlichkeitssuche ausgeführt "
                    f"(Limit: {MAX_SIMILAR_LOOKUPS} Abfragen)."
                )

        scored.append({
            "id": ident,
            "title": str(
                ad.get("title") or "Fahrzeugangebot"
            ),
            "price": round(price),
            "estimated_market_value": market,
            "cost_reserve": cost_reserve,
            "estimated_profit": profit,
            "profit_target": MIN_TARGET_PROFIT,
            "status": status,
            "comparable_ads": comp_count,
            "estimation_note": note,
            "location": location_text(ad),
            "created_at": ad.get("created_at"),
            "url": ad_url_of(ad)
        })

    scored.sort(key=lambda item: (
        item["estimated_profit"] is None,
        -(item["estimated_profit"] or -999999),
        item["price"]
    ))

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "location": LOCATION_NAME,
        "radius_km": RADIUS_KM,
        "min_price": MIN_PRICE,
        "max_price": MAX_PRICE,
        "searches_run": len(queries),
        "ads_found": len(unique),
        "cars_evaluated": len(candidates),
        "similar_checks_run": len(similar_results),
        "similar_checks_limit": MAX_SIMILAR_LOOKUPS,
        "min_target_profit": MIN_TARGET_PROFIT,
        "ads": scored,
        "errors": search_errors,
        "note": (
            "Marktwerte sind unverbindliche Schätzungen aus ähnlichen "
            "Live-Inseraten. Die Ähnlichkeitssuche liefert höchstens "
            "10 Treffer und gleicht Baujahr, Kilometerstand, "
            "Motorisierung und Zustand nicht zwingend exakt ab. "
            "Vor dem Kauf Inserat und Fahrzeug sorgfältig prüfen."
        )
    }

    write_json(OUT, result)
    write_json(STATE, state)

    profitable = sum(
        1 for item in scored
        if item.get("estimated_profit") is not None
        and item["estimated_profit"] >= MIN_TARGET_PROFIT
    )

    print(
        f"Fertig: {len(candidates)} Fahrzeuge; "
        f"{len(similar_results)} Ähnlichkeitsabfragen; "
        f"{profitable} Angebote mit geschätztem Gewinn "
        f">= {MIN_TARGET_PROFIT} EUR."
    )

    if search_errors:
        print(
            "Suchfehler: " + " | ".join(search_errors)
        )


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print("FEHLER:", exc, file=sys.stderr)
        sys.exit(1)
