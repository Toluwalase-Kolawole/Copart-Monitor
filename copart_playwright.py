"""
Playwright-based fallback scraper for Copart.
Uses network response interception to capture Copart's internal
search API calls — more reliable than DOM scraping.
"""

import logging
import time

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.copart.com/lotSearchResults/"


def _build_search_url(makes, damage_types):
    """Build Copart search URL — navigating here triggers the real API calls."""
    from urllib.parse import quote
    query_parts = []
    if makes:
        encoded = quote(",".join(m.upper() for m in makes), safe=",")
        query_parts.append(f"makeList={encoded}")
    if damage_types:
        encoded = quote(",".join(d.upper() for d in damage_types), safe=",")
        query_parts.append(f"damageList={encoded}")
    query = "&".join(query_parts)
    return f"{SEARCH_URL}?{query}" if query else SEARCH_URL


def _matches_filters(raw, makes, damage_types, year_min=None, year_max=None, max_odometer=None):
    """Check if a raw lot matches the requested filters."""
    # Make filter
    if makes:
        lot_make = (raw.get("mkn") or raw.get("mk") or "").upper()
        if not any(m.upper() in lot_make for m in makes):
            return False

    # Damage filter
    if damage_types:
        lot_damage = (raw.get("dd") or raw.get("dmg") or "").upper()
        if not any(d.upper() in lot_damage for d in damage_types):
            return False

    # Year filter
    lot_year = raw.get("y")
    if lot_year:
        try:
            y = int(lot_year)
            if year_min and y < year_min:
                return False
            if year_max and y > year_max:
                return False
        except (ValueError, TypeError):
            pass

    # Odometer filter — exclude lots that exceed max miles
    # Lots with no odometer reading are kept (unknown mileage)
    if max_odometer is not None:
        raw_odo = raw.get("orr") or raw.get("od")
        if raw_odo is not None:
            try:
                odo = int(str(raw_odo).replace(",", "").strip())
                if odo > max_odometer:
                    return False
            except (ValueError, TypeError):
                pass

    return True


def search_playwright(makes, damage_types, year_min=None, year_max=None, max_odometer=None, max_pages=3):
    """
    Open Copart in headless Chromium and intercept the internal
    search API responses — much more reliable than DOM scraping.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        raise RuntimeError("playwright not installed.")

    url = _build_search_url(makes, damage_types)
    intercepted = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.new_page()

        # Intercept Copart's internal search API responses
        def handle_response(response):
            try:
                if response.status != 200:
                    return
                ct = response.headers.get("content-type", "")
                if "application/json" not in ct:
                    return
                resp_url = response.url
                if not any(k in resp_url for k in ["search", "lot", "result", "copart.com"]):
                    return
                data = response.json()
                # Try multiple response shapes
                content = (
                    data.get("data", {}).get("results", {}).get("content")
                    or data.get("returnObject", {}).get("results", {}).get("content")
                    or data.get("results", {}).get("content")
                    or []
                )
                if content:
                    logger.info("Intercepted %d lots from: %s", len(content), resp_url)
                    intercepted.extend(content)
            except Exception as e:
                logger.debug("Response intercept error: %s", e)

        page.on("response", handle_response)

        logger.info("Playwright: navigating to %s", url)
        try:
            page.goto(url, wait_until="networkidle", timeout=45_000)
        except PWTimeout:
            logger.warning("networkidle timeout — checking what loaded")

        time.sleep(5)

        try:
            logger.info("Page title: %s", page.title())
            logger.info("Page URL after load: %s", page.url)
        except Exception:
            pass

        # Paginate if we got results and need more
        if intercepted and max_pages > 1:
            for pg in range(1, max_pages):
                try:
                    next_btn = page.query_selector(
                        "button[aria-label='Next page'], "
                        "a[aria-label='Next page'], "
                        "li.pagination-next:not(.disabled) a"
                    )
                    if not next_btn:
                        break
                    next_btn.click()
                    time.sleep(3)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PWTimeout:
                        pass
                except Exception as e:
                    logger.debug("Pagination error: %s", e)
                    break

        browser.close()

    # Apply strict client-side filtering to remove non-matching lots
    before = len(intercepted)
    filtered = [
        raw for raw in intercepted
        if _matches_filters(raw, makes, damage_types, year_min, year_max, max_odometer)
    ]
    logger.info(
        "Client-side filter: %d intercepted → %d matched (makes=%s, damage=%s, years=%s-%s, max_odo=%s)",
        before, len(filtered), makes, damage_types, year_min or "*", year_max or "*", max_odometer or "*",
    )

    results = [_parse_lot(raw) for raw in filtered]
    results = [r for r in results if r.get("lot_number")]
    logger.info("Playwright returned %d lots", len(results))
    return results


def _parse_lot(raw):
    """Normalize a raw Copart lot dict."""
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
