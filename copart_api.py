"""
Copart US API client — correct search-results endpoint with working Solr filters.
"""

import httpx
import logging

logger = logging.getLogger(__name__)

HOME_URL   = "https://www.copart.com/"
SEARCH_URL = "https://www.copart.com/public/lots/search-results"

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.copart.com",
    "Referer": "https://www.copart.com/lotSearchResults",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}


def build_payload(makes, damage_types, year_min=None, year_max=None, page=0, rows=100):
    """
    Build Solr-style filter payload — same format as Copart UK (confirmed working).
    Uses OR logic within each filter group.
    """
    filters = {}

    # Year range
    y_from = year_min or 1900
    y_to   = year_max or 2100
    filters["YEAR"] = [f"lot_year:[{y_from} TO {y_to}]"]

    # Makes — OR between each make
    if makes:
        filters["MAKE"] = [f'make:"{m.upper()}"' for m in makes]

    # Damage types — OR between each damage type
    if damage_types:
        filters["PRID"] = [f'damage_description:"{d.upper()}"' for d in damage_types]

    # Country
    filters["AUCTION_COUNTRY_CODE"] = ["auction_country_code:US"]

    return {
        "query": ["*"],
        "filter": filters,
        "sort": ["auction_date_utc asc"],
        "page": page,
        "size": rows,
        "start": page * rows,
        "watchListOnly": False,
        "freeFormSearch": False,
        "hideImages": False,
        "defaultSort": False,
        "specificRowProvided": False,
        "displayName": "",
        "searchName": "",
        "backUrl": "",
        "includeTagByField": {},
        "rawParams": {},
    }


def parse_lot(raw):
    lot_number = str(raw.get("ln") or raw.get("lotNumberStr") or "")
    year   = raw.get("lcy") or raw.get("y")
    make   = raw.get("mkn") or raw.get("mk")
    model  = raw.get("lm")  or raw.get("mdn") or raw.get("md")
    damage = raw.get("dd")  or raw.get("dmg")
    return {
        "lot_number": lot_number,
        "title": raw.get("ld") or f"{year or ''} {make or ''} {model or ''}".strip(),
        "year": year,
        "make": make,
        "model": model,
        "damage": damage,
        "odometer": raw.get("orr") or raw.get("od"),
        "sale_date": raw.get("ad"),
        "location": raw.get("yn"),
        "estimate": raw.get("la") or raw.get("lv"),
        "image_url": raw.get("tims"),
        "url": f"https://www.copart.com/lot/{lot_number}/{raw.get('ldu', '')}".rstrip("/"),
    }


def _passes_filters(lot, makes, damage_types, year_min, year_max, max_odometer):
    # Make
    if makes:
        lot_make = (lot.get("make") or "").upper()
        if not any(m.upper() in lot_make for m in makes):
            return False
    # Damage
    if damage_types:
        lot_damage = (lot.get("damage") or "").upper()
        if not any(d.upper() in lot_damage for d in damage_types):
            return False
    # Year (only filter if known)
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
    # Odometer (only filter if known)
    if max_odometer and lot.get("odometer") is not None:
        try:
            odo = int(str(lot["odometer"]).replace(",", "").strip())
            if odo > max_odometer:
                return False
        except (ValueError, TypeError):
            pass
    return True


def search_api(makes, damage_types, year_min=None, year_max=None, max_odometer=None, max_pages=3):
    results = []

    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        # Get session cookies
        try:
            home = client.get(HOME_URL)
            logger.info("Homepage: status=%d cookies=%s", home.status_code, list(client.cookies.keys()))
        except Exception as e:
            logger.warning("Homepage fetch failed: %s", e)

        for page in range(max_pages):
            payload = build_payload(makes, damage_types, year_min=year_min, year_max=year_max, page=page)
            logger.debug("Payload filters: %s", payload["filter"])

            try:
                resp = client.post(SEARCH_URL, json=payload)
                logger.info("Search page=%d status=%d", page, resp.status_code)
                resp.raise_for_status()
            except Exception as e:
                logger.warning("Search failed: %s", e)
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

            logger.info("Page %d: %d lots returned (totalElements=%d, totalPages=%d)",
                page, len(content), total_elements, total_pages)

            if not content:
                break

            # Log sample to verify filters are working
            if page == 0 and content:
                logger.info("SAMPLE: make=%s damage=%s year=%s odo=%s",
                    content[0].get("mkn"), content[0].get("dd"),
                    content[0].get("lcy"), content[0].get("orr"))

            for raw in content:
                lot = parse_lot(raw)
                if _passes_filters(lot, makes, damage_types, year_min, year_max, max_odometer):
                    results.append(lot)
                else:
                    logger.debug("SKIP %s | %s | %s | yr=%s | odo=%s",
                        lot["lot_number"], lot["make"], lot["damage"],
                        lot["year"], lot["odometer"])

            logger.info("After page %d: %d lots pass filters", page, len(results))

            if page + 1 >= total_pages:
                break

    logger.info("API returned %d lots total", len(results))
    return results
