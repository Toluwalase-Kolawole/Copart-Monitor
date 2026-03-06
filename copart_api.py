"""
Copart API client.
Uses session cookies from homepage, then calls the correct search endpoint.
"""

import httpx
import logging

logger = logging.getLogger(__name__)

# Correct Copart search endpoint (v2)
SEARCH_URL = "https://www.copart.com/public/lots/search"
HOME_URL   = "https://www.copart.com/"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.copart.com",
    "Referer": "https://www.copart.com/lotSearchResults/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

# All known Copart search endpoint variations to try
SEARCH_ENDPOINTS = [
    "https://www.copart.com/public/lots/search",
    "https://www.copart.com/public/lots/search/US",
    "https://api.copart.com/public/lots/search",
    "https://api.copart.com/public/lots/search/US",
    "https://www.copart.com/public/user/lots/search",
    "https://www.copart.com/public/lots/search/query",
]


def build_payload(makes, damage_types, year_min=None, year_max=None, page=0, rows=100):
    filter_list = []
    if makes:
        filter_list.append({
            "displayName": "Make",
            "name": "make",
            "values": [m.upper() for m in makes],
        })
    if damage_types:
        filter_list.append({
            "displayName": "Primary Damage",
            "name": "primaryDamage",
            "values": [d.upper() for d in damage_types],
        })

    payload = {
        "query": ["*"],
        "filter": {
            "SALE_STATUS": ["On Time, Sold"],
            "AUCTION_COUNTRY_CODE": ["US"],
        },
        "sort": {"auction_date_type": "desc"},
        "page": page,
        "size": rows,
        "start": page * rows,
        "watchListOnly": False,
        "freeFormFilters": filter_list,
        "defaultSort": False,
    }

    if year_min is not None or year_max is not None:
        payload["filter"]["YEAR"] = {
            "from": str(year_min) if year_min else "*",
            "to": str(year_max) if year_max else "*",
        }

    return payload


def parse_lot(raw):
    lot_number = str(raw.get("lotNumberStr") or raw.get("ln") or "")
    return {
        "lot_number": lot_number,
        "title": (
            raw.get("ld")
            or f"{raw.get('y', '')} {raw.get('mkn', '')} {raw.get('mdn', '')}".strip()
        ),
        "year": raw.get("y"),
        "make": raw.get("mkn") or raw.get("mk"),
        "model": raw.get("mdn") or raw.get("md"),
        "damage": raw.get("dd") or raw.get("dmg"),
        "odometer": raw.get("orr") or raw.get("od"),
        "sale_date": raw.get("ad") or raw.get("saleDate"),
        "location": raw.get("yn") or raw.get("yardName"),
        "estimate": raw.get("la") or raw.get("lv"),
        "image_url": raw.get("tims") or raw.get("imgUrl"),
        "url": f"https://www.copart.com/lot/{lot_number}",
    }


def _passes_filters(lot, makes, damage_types, year_min, year_max, max_odometer):
    """Strict client-side filter — applied to every lot regardless of API filters."""
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
    # Year
    if lot.get("year"):
        try:
            y = int(lot["year"])
            if year_min and y < year_min:
                return False
            if year_max and y > year_max:
                return False
        except (ValueError, TypeError):
            pass
    # Odometer
    if max_odometer and lot.get("odometer"):
        try:
            odo = int(str(lot["odometer"]).replace(",", "").strip())
            if odo > max_odometer:
                return False
        except (ValueError, TypeError):
            pass
    return True


def _find_working_endpoint(client, payload):
    """Try each known endpoint and return (url, response_data) for the first that works."""
    for url in SEARCH_ENDPOINTS:
        try:
            resp = client.post(url, json=payload, timeout=15)
            logger.info("Tried %s → status %d", url, resp.status_code)
            if resp.status_code == 200:
                data = resp.json()
                content = (
                    data.get("data", {}).get("results", {}).get("content")
                    or data.get("returnObject", {}).get("results", {}).get("content")
                    or data.get("results", {}).get("content")
                )
                if content is not None:
                    logger.info("✅ Working endpoint found: %s (%d lots)", url, len(content))
                    return url, data
        except Exception as e:
            logger.debug("Endpoint %s failed: %s", url, e)
    return None, None


def search_api(makes, damage_types, year_min=None, year_max=None, max_odometer=None, max_pages=3):
    """Two-step: get session cookies from homepage, then search."""
    results = []

    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        # Step 1 — get session cookies
        try:
            logger.info("Fetching Copart homepage for session cookies...")
            home_resp = client.get(HOME_URL)
            logger.info("Homepage: status=%d cookies=%s", home_resp.status_code, list(client.cookies.keys()))
        except Exception as e:
            logger.warning("Homepage fetch failed: %s", e)

        # Step 2 — find working endpoint on first page
        payload = build_payload(makes, damage_types, year_min=year_min, year_max=year_max, page=0)
        working_url, first_data = _find_working_endpoint(client, payload)

        if not working_url:
            raise RuntimeError("No working Copart API endpoint found")

        # Process first page
        def extract_content(data):
            return (
                data.get("data", {}).get("results", {}).get("content")
                or data.get("returnObject", {}).get("results", {}).get("content")
                or data.get("results", {}).get("content")
                or []
            )

        def extract_total_pages(data):
            return (
                data.get("data", {}).get("results", {}).get("totalPages")
                or data.get("returnObject", {}).get("results", {}).get("totalPages")
                or data.get("results", {}).get("totalPages")
                or 1
            )

        content = extract_content(first_data)
        total_pages = extract_total_pages(first_data)
        logger.info("Page 0: %d lots, total_pages=%d", len(content), total_pages)

        before = 0
        for raw in content:
            lot = parse_lot(raw)
            before += 1
            if _passes_filters(lot, makes, damage_types, year_min, year_max, max_odometer):
                results.append(lot)

        # Remaining pages
        for page in range(1, min(max_pages, total_pages)):
            payload = build_payload(makes, damage_types, year_min=year_min, year_max=year_max, page=page)
            resp = client.post(working_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = extract_content(data)
            logger.info("Page %d: %d lots", page, len(content))
            if not content:
                break
            for raw in content:
                lot = parse_lot(raw)
                before += 1
                if _passes_filters(lot, makes, damage_types, year_min, year_max, max_odometer):
                    results.append(lot)

        logger.info("Client-side filter: %d fetched → %d matched (makes=%s damage=%s years=%s-%s max_odo=%s)",
            before, len(results), makes, damage_types, year_min or "*", year_max or "*", max_odometer or "*")

    logger.info("API returned %d lots after filtering", len(results))
    return results
