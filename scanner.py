import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Kleinanzeigen-Agent API: keine kostenpflichtigen Ähnlichkeitsabfragen
BASE = "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen"
KEY = os.environ.get("KLAZ_API_KEY", "").strip()

STATE = "data/state.json"
OUT = "data/results.json"

LOCATION_NAME = "Kassel"
RADIUS_KM = 100
MIN_PRICE = 100
MAX_PRICE = 7000
MIN_TARGET_PROFIT = 800

SEARCH_TERMS = ("Auto", "Gebrauchtwagen", "PKW")

MAKES = (
    "volkswagen", "vw", "opel", "ford", "bmw", "mercedes", "benz",
    "audi", "skoda", "seat", "renault", "peugeot", "citroen",
    "citroën", "dacia", "volvo", "fiat", "toyota", "hyundai",
    "kia", "mazda", "honda", "nissan", "suzuki", "mini",
    "mitsubishi", "subaru", "chevrolet", "alfa romeo", "smart",
    "porsche", "saab", "jaguar", "land rover", "jeep", "daihatsu",
    "chrysler", "tesla", "lada", "lancia", "mg", "lexus"
)

MODEL_TERMS = (
    "golf", "polo", "passat", "jetta", "lupo", "fox", "touran",
    "tiguan", "caddy", "corsa", "astra", "insignia", "vectra",
    "focus", "fiesta", "mondeo", "ka", "clio", "megane", "twingo",
    "laguna", "modus", "octavia", "fabia", "superb", "leon", "ibiza",
    "a1", "a2", "a3", "a4", "a5", "a6", "a8", "3er", "5er", "1er",
    "x1", "x3", "x5", "c klasse", "e klasse", "a klasse", "b klasse",
    "c180", "c200", "e200", "e220", "yaris", "corolla", "avensis",
    "auris", "ceed", "rio", "i10", "i20", "i30", "mazda 2", "mazda 3",
    "civic", "jazz", "micra", "qashqai", "swift", "ignis", "mini",
    "fortwo", "forfour", "c1", "c2", "c3", "c4", "107", "206", "207",
    "208", "306", "307", "308", "106", "fiat 500", "panda", "punto",
    "multipla", "v40", "v50", "v70", "s40", "s60", "xc90",
    "zafira", "meriva", "mokka", "captur", "logan", "sandero"
)

EXCLUDE_WORDS = (
    "autoanhänger", "anhaenger", "anhänger", "felge", "reifen",
    "motoröl", "ersatzteil", "scheinwerfer", "getriebeöl",
    "suche auto", "schlachtfest", "nur teile", "autoteile",
    "teileträger", "auto gesucht", "kaufgesuch"
)

MAJOR_DEFECTS = (
    "motorschaden", "getriebeschaden", "unfallwagen",
    "bastlerfahrzeug", "bastlerauto", "nicht fahrbereit",
    "springt nicht an", "kupplung defekt", "zylinderkopfschaden",
    "rahmenschaden", "wasserschaden"
)

MODERATE_DEFECTS = (
    "tüv abgelaufen", "tuev abgelaufen", "rost", "klima defekt",
    "reparatur", "defekt", "ölverlust", "oelverlust",
    "motorkontrollleuchte", "leuchtet", "unfall", "getriebe ruckelt"
)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def api(path, params=None):
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. GitHub → Settings → Secrets and variables → Actions prüfen."
        )

    url = BASE + path
    if params:
        url += "?" + urlencode(params)

    req = Request(url, headers={
        "klaz_key": KEY,
        "Accept": "application/json",
        "User-Agent": "AutoDealScanner/3.0"
    })

    try:
        with urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:700]
        raise RuntimeError(f"API HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"API-Verbindung fehlgeschlagen: {exc}") from exc

    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(payload.get("message", "API-Anfrage fehlgeschlagen"))

    return payload


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def find_id(payload, accepted_names, field_names):
    matches = []

    for item in walk(payload):
        if not isinstance(item, dict):
            continue

        name = " ".join(
            str(item.get(k, "")) for k in ("name", "city", "label")
        ).strip().lower()

        ident = next(
            (item.get(field) for field in field_names if item.get(field) is not None),
            None
        )

        if ident is None:
            continue

        if any(name == n for n in accepted_names):
            matches.append((0, str(ident)))
        elif any(name.startswith(n) for n in accepted_names):
            matches.append((1, str(ident)))

    return sorted(matches)[0][1] if matches else None


def extract_ads(payload):
    if not isinstance(payload, dict):
        return []

    data = payload.get("data", {})

    if isinstance(data, dict) and isinstance(data.get("ads"), list):
        return data["ads"]

    if isinstance(payload.get("ads"), list):
        return payload["ads"]

    if isinstance(data, list):
        return data

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
    value = None

    if isinstance(seller, dict):
        value = (
            seller.get("type")
            or seller.get("account_type")
            or seller.get("seller_type")
        )

    return str(
        value or ad.get("poster_type") or ad.get("seller_type") or ""
    ).strip().upper()


def is_private(ad):
    # Unbekannte Verkäuferarten werden nicht automatisch als privat gewertet.
    return seller_type_of(ad) in ("PRIVATE", "PRIVATE_SELLER", "PRIVAT")


def ad_text(ad):
    return (
        str(ad.get("title") or "")
        + " "
        + str(ad.get("description") or "")
    ).lower()


def is_car(ad):
    text = ad_text(ad)

    if any(word in text for word in EXCLUDE_WORDS):
        return False

    has_make = any(
        re.search(r"(?<![a-z])" + re.escape(make) + r"(?![a-z])", text)
        for make in MAKES
    )

    has_model = any(model in text for model in MODEL_TERMS)

    return has_make or has_model or any(
        word in text for word in ("gebrauchtwagen", "pkw", "kleinwagen", "fahrzeug")
    )


def location_text(ad):
    loc = ad.get("location") or {}

    if isinstance(loc, dict):
        return str(loc.get("city") or loc.get("name") or LOCATION_NAME)

    return str(loc or LOCATION_NAME)


def normalize(text):
    text = str(text or "").lower().replace("-", " ").replace("/", " ")
    text = re.sub(r"[^a-z0-9äöüß ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def model_key(ad):
    title = normalize(ad.get("title"))

    make = next(
        (
            m for m in sorted(MAKES, key=len, reverse=True)
            if re.search(r"(?<![a-z])" + re.escape(m) + r"(?![a-z])", title)
        ),
        None
    )

    model = next(
        (
            m for m in sorted(MODEL_TERMS, key=len, reverse=True)
            if re.search(r"(?<![a-z0-9])" + re.escape(m) + r"(?![a-z0-9])", title)
        ),
        None
    )

    if make and model:
        make_norm = "vw" if make == "volkswagen" else (
            "mercedes" if make == "benz" else make
        )
        return f"{make_norm} {model}"

    if model:
        return model

    return ""


def reserve_for(ad, price):
    text = ad_text(ad)

    if any(word in text for word in MAJOR_DEFECTS):
        return max(1500, price * 0.35)

    if any(word in text for word in MODERATE_DEFECTS):
        return max(900, price * 0.18)

    return max(600, price * 0.10)


def evaluate(candidates):
    # Verglichen werden nur Suchtreffer, die bereits vorliegen.
    groups = {}

    for ad in candidates:
        key = model_key(ad)
        price = price_of(ad)

        if key and price is not None:
            groups.setdefault(key, []).append(price)

    results = []

    for ad in candidates:
        price = price_of(ad)
        key = model_key(ad)

        peer_prices = [
            p for p in groups.get(key, [])
            if p != price and 100 <= p <= 50000
        ]

        peer_prices = [
            p for p in peer_prices
            if price * 0.55 <= p <= price * 2.5
        ]
        peer_prices.sort()

        market = None
        note = (
            "Nicht ausreichend viele vergleichbare private Suchtreffer "
            "für eine Marktwertschätzung."
        )

        if key and len(peer_prices) >= 3:
            n = len(peer_prices)

            if n % 2:
                market = round(peer_prices[n // 2])
            else:
                market = round(
                    (peer_prices[n // 2 - 1] + peer_prices[n // 2]) / 2
                )

            note = (
                f"Median aus {n} anderen privaten Suchtreffern mit Modell-Schlüssel "
                f"„{key}“. Baujahr, Laufleistung, Motor und Zustand werden nicht exakt "
                "abgeglichen."
            )

        repair_reserve = round(reserve_for(ad, price))
        other_costs = round(max(250, price * 0.08))
        total_costs = repair_reserve + other_costs

        profit = (
            round(market - price - total_costs)
            if market is not None
            else None
        )

        if profit is not None and profit >= MIN_TARGET_PROFIT:
            status = "MANUELL PRÜFEN – möglicher Treffer"
        elif profit is not None and profit >= 0:
            status = "Möglicher kleiner Gewinn"
        elif profit is not None:
            status = "Kein rechnerischer Gewinn"
        else:
            status = "NICHT AUSREICHEND BEWERTBAR"

        results.append({
            "id": ad_id_of(ad),
            "title": str(ad.get("title") or "Fahrzeugangebot"),
            "price": round(price),
            "estimated_market_value": market,
            "cost_reserve": total_costs,
            "estimated_profit": profit,
            "profit_target": MIN_TARGET_PROFIT,
            "status": status,
            "seller_type": seller_type_of(ad),
            "comparable_ads": len(peer_prices),
            "model_key": key or None,
            "estimation_note": note,
            "location": location_text(ad),
            "created_at": ad.get("created_at"),
            "url": ad_url_of(ad)
        })

    results.sort(key=lambda item: (
        item["estimated_profit"] is None,
        -(
            item["estimated_profit"]
            if item["estimated_profit"] is not None
            else -999999
        ),
        item["price"]
    ))

    return results


def main():
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY ist nicht gesetzt. GitHub → Settings → Secrets and variables → Actions prüfen."
        )

    state = read_json(STATE, {})
    location_id = state.get("location_id")
    category_id = state.get("category_id")

    if not location_id:
        payload = api("/locations", {"q": LOCATION_NAME, "limit": 100})
        location_id = find_id(payload, {"kassel"}, ("location_id", "id"))

        if not location_id:
            raise RuntimeError(
                "Kassel wurde in der Standortantwort nicht gefunden; "
                "es wird kein anderer Ort verwendet."
            )

    if not category_id:
        payload = api("/categories")
        category_id = find_id(
            payload,
            {"autos", "pkw", "autos & motorräder"},
            ("category_id", "id")
        )

        if not category_id:
            raise RuntimeError("Auto-Kategorie-ID konnte nicht ermittelt werden.")

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

                ident = ad_id_of(ad) or ad_url_of(ad)

                if ident:
                    unique[ident] = ad

            # Kleine Pause zur Verringerung unnötiger Anfrage-Spitzen.
            time.sleep(2)

        except Exception as exc:
            search_errors.append(f"{query}: {exc}")

    if not unique and search_errors:
        raise RuntimeError(
            "Alle Suchanfragen fehlgeschlagen: " + " | ".join(search_errors)
        )

    candidates = [
        ad for ad in unique.values()
        if is_private(ad)
        and price_of(ad) is not None
        and MIN_PRICE <= price_of(ad) <= MAX_PRICE
        and is_car(ad)
    ]

    scored = evaluate(candidates)

    profitable = sum(
        1 for item in scored
        if item["estimated_profit"] is not None
        and item["estimated_profit"] >= MIN_TARGET_PROFIT
    )

    comparable = sum(
        1 for item in scored
        if item["estimated_market_value"] is not None
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
        "similar_checks_run": 0,
        "similar_checks_limit": 0,
        "min_target_profit": MIN_TARGET_PROFIT,
        "profitable_offers": profitable,
        "market_values_estimated": comparable,
        "ads": scored,
        "errors": search_errors,
        "note": (
            "Es werden keine Ähnlichkeits-API-Aufrufe ausgeführt. Marktwerte sind grobe "
            "Mediane aus bereits gefundenen privaten Inseraten mit ähnlichem Modellnamen. "
            "Baujahr, Kilometerstand, Motorisierung, TÜV und Zustand werden nicht zuverlässig "
            "abgeglichen. Kosten und Gewinn müssen vor dem Kauf manuell geprüft werden."
        )
    }

    write_json(OUT, result)
    write_json(STATE, state)

    print(
        f"Fertig: {len(unique)} Anzeigen gefunden; "
        f"{len(candidates)} private Fahrzeuge ausgewertet; "
        f"{comparable} mit grobem Vergleichswert; "
        f"{profitable} mögliche Treffer mit Gewinn >= {MIN_TARGET_PROFIT} EUR."
    )

    if search_errors:
        print("Suchfehler: " + " | ".join(search_errors))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FEHLER:", exc, file=sys.stderr)
        sys.exit(1)
