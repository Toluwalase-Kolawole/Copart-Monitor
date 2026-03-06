"""
Copart US API client.
Uses YEAR filter only (confirmed working) + client-side filtering for everything else.
Damage/make filtered client-side since server-side field names are unverified for US.
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


def build_payload(year_min=None, year_max=None, page=0, rows=100):
    """
    Minimal payload — only YEAR and COUNTRY filters (both confirmed working).
    Make/damage/odometer are filtered client-side.
    """
    filters = {
        "AUCTION_COUNTRY_CODE": ["auction_country_code:US"],
    }

    if year_min and year_max:
        filters["YEAR"] = [f"lot_year:[{year_min} TO {year_max}]"]
    elif year_min:
        filters["YEAR"] = [f"lot_year:[{year_min} TO *]"]
    elif year_max:
        filters["YEAR"] = [f"lot_year:[* TO {year_max}]"]

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
    year  = raw.get("lcy") or raw.get("y")
    make  = raw.get("mkn") or raw.get("mk")
    model = raw.get("lm")  or raw.get("mdn") or raw.get("md")
    damage = raw.get("dd") or raw.get("dmg")
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
    if makes:
        lot_make = (lot.get("make") or "").upper()
        if not any(m.upper() in lot_make for m in makes):
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


def search_api(makes, damage_types, year_min=None, year_max=None,
               max_odometer=None, max_pages=3):
    results = []

    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        try:
            home = client.get(HOME_URL)
            logger.info("Homepage: status=%d cookies=%s",
                        home.status_code, list(client.cookies.keys()))
        except Exception as e:
            logger.warning("Homepage fetch failed: %s", e)

        for page in range(max_pages):
            payload = build_payload(year_min=year_min, year_max=year_max, page=page)

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
                logger.warning("Empty content on page %d. Response: %s",
                               page, resp.text[:300])
                break

            if page == 0:
                logger.info("SAMPLE: make=%s damage=%s year=%s odo=%s",
                    content[0].get("mkn"), content[0].get("dd"),
                    content[0].get("lcy"), content[0].get("orr"))

            before = len(results)
            for raw in content:
                lot = parse_lot(raw)
                if _passes_filters(lot, makes, damage_types, year_min, year_max, max_odometer):
                    results.append(lot)

            logger.info("Page %d: %d passed filters (running total: %d)",
                        page, len(results) - before, len(results))

            if page + 1 >= total_pages:
                logger.info("Reached last page (%d)", total_pages)
                break

    logger.info("API returned %d lots total", len(results))
    return results
