"""
Copart US API client.
All vehicle criteria (makes, models) are passed in from environment variables.
NLR flag is tracked per-lot so Telegram can label them distinctly.
"""

import httpx
import logging

logger = logging.getLogger(__name__)

HOME_URL   = "https://www.copart.com/"
SEARCH_URL = "https://www.copart.com/public/lots/search-results"

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
    "Origin": "https://www.copart.com",
    "Referer": "https://www.copart.com/lotSearchResults",
    "Cache-Control": "max-age=0",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

# Confirmed damage codes
DAMAGE_CODES = {
    "FRONT END":            "DAMAGECODE_FR",
    "HAIL":                 "DAMAGECODE_HL",
    "ALL OVER":             "DAMAGECODE_AO",
    "MINOR DENT/SCRATCHES": "DAMAGECODE_MN",
    "NORMAL WEAR":          "DAMAGECODE_NW",
    "REAR END":             "DAMAGECODE_RR",
    "SIDE":                 "DAMAGECODE_SD",
    "VANDALISM":            "DAMAGECODE_VN",
}

# Secondary damage values to exclude
AIRBAG_EXCLUSIONS = {"DEPLOYED AIRBAGS", "BIOHAZARD", "BURN", "STRIPPED"}


def build_payload(makes, models, damage_types, year_min=None, year_max=None,
                  max_odometer=None, nlr_only=False, page=0, rows=100):
    """
    Build the Copart search payload.
    nlr_only=True  → add FETI/MISC filter (No License Required only)
    nlr_only=False → no license filter (all lots)
    """
    filters = {}

    # Makes
    if makes:
        filters["MAKE"] = [f'lot_make_desc:"{m.upper()}"' for m in makes]

    # Models — passed in from env (e.g. RAV4, RAV4 HYBRID, SONATA …)
    if models:
        filters["MODL"] = [f'lot_model_desc:"{m.upper()}"' for m in models]

    # Damage
    if damage_types:
        prid = []
        for d in damage_types:
            code = DAMAGE_CODES.get(d.upper())
            if code:
                prid.append(f"damage_type_code:{code}")
        if prid:
            filters["PRID"] = prid

    # Odometer
    max_odo = max_odometer or 9999999
    filters["ODM"] = [f"odometer_reading_received:[0 TO {max_odo}]"]

    # Year
    if year_min and year_max:
        filters["YEAR"] = [f"lot_year:[{year_min} TO {year_max}]"]
    elif year_min:
        filters["YEAR"] = [f"lot_year:[{year_min} TO *]"]
    elif year_max:
        filters["YEAR"] = [f"lot_year:[* TO {year_max}]"]

    # No License Required filter — only applied when nlr_only=True
    if nlr_only:
        filters["FETI"] = ["lot_features_code:LOTFEATURE_0"]
        filters["MISC"] = ["lot_features_code:LOTFEATURE_0"]

    payload = {
        "query": ["*"],
        "filter": filters,
        "sort": ["relevancy desc", "auction_date_type desc", "auction_date_utc asc"],
        "page": page,
        "size": rows,
        "start": page * rows,
        "watchListOnly": False,
        "freeFormSearch": False,
        "hideImages": False,
        "defaultSort": True,
        "specificRowProvided": False,
        "displayName": "",
        "searchName": "",
        "backUrl": "",
        "includeTagByField": {"MISC": "{!tag=FETI}"},
        "rawParams": {},
    }
    return payload


def parse_lot(raw):
    lot_number = str(raw.get("ln") or raw.get("lotNumberStr") or "")
    year  = raw.get("lcy") or raw.get("y")
    make  = raw.get("mkn") or raw.get("mk")
    model = raw.get("lm")  or raw.get("mdn") or raw.get("md")
    damage = raw.get("dd") or raw.get("dmg")
    secondary_damage = raw.get("sdd") or raw.get("sd") or ""

    # NLR detection — lfd is a list of feature labels e.g. ["No License Required", ...]
    features = raw.get("lfd") or []
    is_nlr = any("no license" in str(f).lower() for f in features)

    return {
        "lot_number": lot_number,
        "title": raw.get("ld") or f"{year or ''} {make or ''} {model or ''}".strip(),
        "year": year,
        "make": make,
        "model": model,
        "damage": damage,
        "secondary_damage": secondary_damage,
        "odometer": raw.get("orr") or raw.get("od"),
        "sale_date": raw.get("ad"),        # Unix ms timestamp
        "location": raw.get("yn"),
        "estimate": raw.get("la") or raw.get("lv"),
        "image_url": raw.get("tims"),
        "is_nlr": is_nlr,                  # ← True = No License Required
        "url": f"https://www.copart.com/lot/{lot_number}/{raw.get('ldu', '')}".rstrip("/"),
    }


def _passes_filters(lot, makes, models, damage_types, year_min, year_max, max_odometer):
    # Exclude high-risk secondary damage
    secondary = (lot.get("secondary_damage") or "").upper()
    if secondary and any(excl in secondary for excl in AIRBAG_EXCLUSIONS):
        return False

    if makes:
        lot_make = (lot.get("make") or "").upper()
        if not any(m.upper() in lot_make for m in makes):
            return False

    # Model filter — exact match against the allowed list (client-side enforcement)
    # The Copart API MODL filter is unreliable and lets unrelated models through
    if models:
        lot_model = (lot.get("model") or "").upper().strip()
        if not any(lot_model == m.upper().strip() for m in models):
            return False

    if damage_types:
        lot_damage = (lot.get("damage") or "").upper()
        if not any(d.upper() in lot_damage for d in damage_types):
            return False

    year = lot.get("year")
    if year is not None:
        try:
            y = int(year)
            if year_min and y < year_min:
                return False
            if year_max and y > year_max:
                return False
        except (ValueError, TypeError):
            pass

    if max_odometer and lot.get("odometer") is not None:
        try:
            odo = int(str(lot["odometer"]).replace(",", "").strip())
            if odo > max_odometer:
                return False
        except (ValueError, TypeError):
            pass

    return True


def search_api(makes, models, damage_types, year_min=None, year_max=None,
               max_odometer=None, max_pages=3):
    """
    Search ALL lots (no NLR filter at API level).
    Each lot has is_nlr=True/False so notifier can label them differently.
    """
    results = []
    rows = 100

    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        try:
            home = client.get(HOME_URL)
            logger.info("Homepage: status=%d cookies=%s",
                        home.status_code, list(client.cookies.keys()))
        except Exception as e:
            logger.warning("Homepage fetch failed: %s", e)

        for page in range(max_pages):
            payload = build_payload(
                makes, models, damage_types,
                year_min=year_min, year_max=year_max,
                max_odometer=max_odometer,
                nlr_only=False,   # fetch ALL — NLR flag is in lot data
                page=page, rows=rows
            )

            try:
                resp = client.post(SEARCH_URL, json=payload)
                logger.info("Search page=%d status=%d", page, resp.status_code)
                resp.raise_for_status()
            except Exception as e:
                logger.warning("Search failed page=%d: %s", page, e)
                break

            data = resp.json()
            content = (
                data.get("data", {}).get("results", {}).get("content")
                or data.get("returnObject", {}).get("results", {}).get("content")
                or []
            )
            total_pages = (
                data.get("data", {}).get("results", {}).get("totalPages")
                or data.get("returnObject", {}).get("results", {}).get("totalPages")
                or 1
            )
            total_elements = (
                data.get("data", {}).get("results", {}).get("totalElements")
                or data.get("returnObject", {}).get("results", {}).get("totalElements")
                or 0
            )

            logger.info("Page %d: %d lots (totalElements=%d totalPages=%d)",
                        page, len(content), total_elements, total_pages)

            if not content:
                logger.warning("Empty content page=%d response=%s",
                               page, resp.text[:300])
                break

            if page == 0:
                logger.info("SAMPLE: make=%s model=%s damage=%s year=%s odo=%s nlr=%s",
                    content[0].get("mkn"), content[0].get("lm"),
                    content[0].get("dd"), content[0].get("lcy"),
                    content[0].get("orr"), content[0].get("lfd"))

            # Correct totalPages bug (Copart always returns 1)
            real_total_pages = max(total_pages, -(-total_elements // rows))
            if real_total_pages != total_pages:
                logger.info("Corrected totalPages: %d -> %d (based on %d elements)",
                            total_pages, real_total_pages, total_elements)

            before = len(results)
            for raw in content:
                lot = parse_lot(raw)
                if _passes_filters(lot, makes, models, damage_types, year_min, year_max, max_odometer):
                    results.append(lot)

            logger.info("Page %d: %d passed filters (running total: %d)",
                        page, len(results) - before, len(results))

            if page + 1 >= real_total_pages:
                logger.info("Reached last page (%d)", real_total_pages)
                break

    nlr_count = sum(1 for l in results if l.get("is_nlr"))
    logger.info("API returned %d lots total (%d NLR, %d need broker)",
                len(results), nlr_count, len(results) - nlr_count)
    return results
