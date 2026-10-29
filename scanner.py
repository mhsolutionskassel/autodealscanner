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

# Kassel fest hinterlegt
KASSEL_LAT = 51.3127
KASSEL_LON = 9.4797
RADIUS_KM = 100
MIN_PRICE = 100
MAX_PRICE = 7000

STATE = "data/state.json"
OUT = "data/results.json"


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


def api(path, params=None):
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY fehlt. GitHub > Settings > Secrets and variables > Actions."
        )

    url = BASE + path
    if params:
        url += "?" + urlencode(params)

    req = Request(
        url,
        headers={
            "klaz_key": KEY,
            "Accept": "application/json",
            "User-Agent": "AutoDealScanner/0.2",
        },
    )

    try:
        with urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))

    except HTTPError as e:
        details = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"API-Fehler HTTP {e.code}: {details}")

    except URLError as e:
        raise RuntimeError(f"API-Verbindung fehlgeschlagen: {e}")

    if data.get("success") is False:
        raise RuntimeError(
            f"API meldet einen Fehler: {data.get('message', data)}"
        )

    return data


def price_of(ad):
    price = ad.get("price")

    if isinstance(price, dict):
        price = price.get("amount")

    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def text_of(ad):
    return (
        str(ad.get("title", ""))
        + " "
        + str(ad.get("description", ""))
    ).lower()


def carish(ad):
    text = text_of(ad)

    exclude = (
        "autoanhänger",
        "anhänger",
        "felge",
        "reifen",
        "motoröl",
        "ersatzteil",
        "scheinwerfer",
        "getriebeöl",
        "suche auto",
        "suche einen pkw",
        "nur ersatzteile",
    )

    brands = (
        "volkswagen", "vw ", "golf", "polo", "passat",
        "opel", "astra", "corsa",
        "ford", "focus", "fiesta",
        "bmw", "mercedes", "benz", "audi",
        "skoda", "seat", "renault", "peugeot",
        "citroën", "citroen", "dacia", "volvo", "fiat",
        "toyota", "hyundai", "kia", "mazda", "honda",
        "nissan", "suzuki", "mitsubishi", "mini",
        "alfa romeo", "smart", "gebrauchtwagen",
        "pkw", "auto ",
    )

    return (
        not any(word in text for word in exclude)
        and any(brand in text for brand in brands)
    )


def repair_reserve(ad, price):
    text = text_of(ad)

    serious = (
        "motorschaden",
        "getriebeschaden",
        "unfallwagen",
        "bastlerfahrzeug",
        "nicht fahrbereit",
        "springt nicht an",
        "kupplung defekt",
    )

    minor = (
        "tüv abgelaufen",
        "tuev abgelaufen",
        "rost",
        "klima defekt",
        "kratzer",
        "delle",
        "reparatur",
        "defekt",
        "ölverlust",
    )

    if any(word in text for word in serious):
        return max(1500, price * 0.35)

    if any(word in text for word in minor):
        return max(900, price * 0.18)

    return max(600, price * 0.10)


def model_key(title):
    words = re.sub(
        r"[^a-z0-9äöüß ]",
        " ",
        title.lower()
    ).split()

    makes = (
        "volkswagen", "vw", "opel", "ford", "bmw",
        "mercedes", "benz", "audi", "skoda", "seat",
        "renault", "peugeot", "citroen", "citroën",
        "dacia", "volvo", "fiat", "toyota", "hyundai",
        "kia", "mazda", "honda", "nissan", "suzuki",
        "mitsubishi", "mini", "smart",
    )

    for index, word in enumerate(words):
        if word in makes:
            if index + 1 < len(words):
                return word + " " + words[index + 1]
            return word

    return " ".join(words[:2])


def get_ads(response):
    """
    Holt Inserate aus dem dokumentierten Antwortformat.
    """
    data = response.get("data", {})

    if isinstance(data, dict):
        ads = data.get("ads", [])
        if isinstance(ads, list):
            return ads

    return []


def main():
    if not KEY:
        raise RuntimeError(
            "KLAZ_API_KEY ist nicht gesetzt. "
            "GitHub > Settings > Secrets and variables > Actions."
        )

    print("Starte AutoDealScanner.")
    print("Standort: Kassel")
    print(f"Koordinaten: {KASSEL_LAT}, {KASSEL_LON}")
    print(f"Suchradius: {RADIUS_KM} km")
    print(f"Preisrahmen: {MIN_PRICE} bis {MAX_PRICE} Euro")
    print("Standort-ID wird nicht benötigt.")

    state = read(STATE, {})
    unique = {}
    errors = []

    # Ohne category_id suchen, um auch die Kategorie-ID-Ermittlung
    # als mögliche Fehlerquelle auszuschließen.
    queries = ("Auto", "Gebrauchtwagen", "PKW")

    for query in queries:
        print(f"Suche: {query}")

        params = {
            "q": query,
            "latitude": KASSEL_LAT,
            "longitude": KASSEL_LON,
            "distance": RADIUS_KM,
            "min_price": MIN_PRICE,
            "max_price": MAX_PRICE,
            "ad_type": "OFFER",
            "page": 0,
            "size": 100,
        }

        try:
            response = api("/search", params)
            ads = get_ads(response)

            print(f"  API hat {len(ads)} Inserate zurückgegeben.")

            for ad in ads:
                ad_id = str(
                    ad.get("ad_id")
                    or ad.get("ad_url")
                    or ""
                )

                if ad_id:
                    unique[ad_id] = ad

        except Exception as exc:
            errors.append(f"{query}: {exc}")
            print(f"  Fehler bei {query}: {exc}")

    if not unique and errors:
        raise RuntimeError(
            "Keine Suche erfolgreich. API-Fehler: "
            + " | ".join(errors)
        )

    candidates = []

    for ad in unique.values():
        price = price_of(ad)

        if price is None:
            continue

        if not MIN_PRICE <= price <= MAX_PRICE:
            continue

        if not carish(ad):
            continue

        candidates.append(ad)

    # Vergleichsgruppen anhand einfacher Modellbegriffe.
    groups = {}

    for ad in candidates:
        key = model_key(str(ad.get("title", "")))
        groups.setdefault(key, []).append(price_of(ad))

    scored = []

    for ad in candidates:
        price = price_of(ad)
        key = model_key(str(ad.get("title", "")))

        comparable = [
            other_price
            for other_price in groups.get(key, [])
            if price * 0.65 <= other_price <= price * 2.5
            and abs(other_price - price) > 100
        ]

        reserve = repair_reserve(ad, price)
        extra_costs = max(250, price * 0.08)

        if len(comparable) >= 2:
            comparable.sort()
            market = comparable[len(comparable) // 2]

            profit = round(
                market - price - reserve - extra_costs
            )

            note = (
                f"{len(comparable)} ähnliche Treffer. "
                "Modell, Baujahr und Kilometerstand nicht vollständig abgeglichen."
            )
        else:
            market = None
            profit = None

            note = (
                "Zu wenige vergleichbare Angebote. "
                "Ein belastbarer Marktwert ist nicht ermittelbar."
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

        scored.append({
            "id": str(ad.get("ad_id") or ""),
            "title": str(ad.get("title") or "Fahrzeugangebot"),
            "price": round(price),
            "estimated_market_value": (
                round(market) if market is not None else None
            ),
            "cost_reserve": round(reserve + extra_costs),
            "estimated_profit": profit,
            "estimation_note": note,
            "location": location_name,
            "created_at": ad.get("created_at"),
            "url": ad.get("ad_url") or "",
        })

    scored.sort(
        key=lambda item: (
            item["estimated_profit"] is None,
            -(item["estimated_profit"] or -99999),
            item["price"],
        )
    )

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "location": "Kassel",
        "radius_km": RADIUS_KM,
        "min_price": MIN_PRICE,
        "max_price": MAX_PRICE,
        "searches_run": len(queries),
        "ads_found": len(unique),
        "cars_evaluated": len(scored),
        "ads": scored,
        "errors": errors,
        "note": (
            "Gewinnschätzungen sind unverbindliche Vorabschätzungen. "
            "Vor dem Kauf Fahrzeug, Marktwert und Reparaturkosten prüfen."
        ),
    }

    write(OUT, result)
    write(STATE, state)

    print("")
    print("SUCHLAUF ABGESCHLOSSEN")
    print(f"Einmalige Inserate: {len(unique)}")
    print(f"Ausgewertete Fahrzeuge: {len(scored)}")
    print(f"Ergebnisdatei: {OUT}")

    profitable = [
        ad for ad in scored
        if ad["estimated_profit"] is not None
        and ad["estimated_profit"] >= 800
    ]

    print(f"Angebote mit geschätztem Gewinn ab 800 Euro: {len(profitable)}")

    if errors:
        print("Einige Suchabfragen hatten Fehler:")
        for error in errors:
            print(" -", error)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        sys.exit(1)
