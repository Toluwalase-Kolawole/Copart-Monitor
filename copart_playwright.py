"""
Playwright-based scraper for Copart US.
Uses real browser to intercept search-results API responses.
Navigates pages by clicking pagination numbers directly.
"""
import logging
import time

logger = logging.getLogger(__name__)


def _build_search_url(makes, damage_types):
    from urllib.parse import quote
    query_parts = []
    if makes:
        query_parts.append(f"makeList={quote(','.join(m.upper() for m in makes), safe=',')}")
    if damage_types:
        query_parts.append(f"damageList={quote(','.join(d.upper() for d in damage_types), safe=',')}")
    query = "&".join(query_parts)
    return f"https://www.copart.com/lotSearchResults/?{query}" if query else "https://www.copart.com/lotSearchResults/"


def _matches_filters(raw, makes, models, damage_types, year_min=None, year_max=None, max_odometer=None):
    if makes:
        lot_make = (raw.get("mkn") or raw.get("mk") or "").upper()
        if not any(m.upper() in lot_make for m in makes):
            return False

    # Strict exact model match — same as copart_api.py
    # If no models configured, block everything (fail-safe)
    if not models:
        return False
    lot_model = (raw.get("lm") or raw.get("mdn") or raw.get("md") or "").upper().strip()
    if not any(lot_model == m.upper().strip() for m in models):
        return False

    if damage_types:
        lot_damage = (raw.get("dd") or raw.get("dmg") or "").upper()
        if not any(d.upper() in lot_damage for d in damage_types):
            return False

    lot_year = raw.get("lcy") or raw.get("y")
    if lot_year is not None:
        try:
            y = int(lot_year)
            if year_min and y < year_min:
                return False
            if year_max and y > year_max:
                return False
        except (ValueError, TypeError):
            pass

    if max_odometer is not None:
        raw_odo = raw.get("orr") or raw.get("od")
        if raw_odo is not None:
            try:
                if int(str(raw_odo).replace(",", "").strip()) > max_odometer:
                    return False
            except (ValueError, TypeError):
                pass

    return True


def _parse_lot(raw):
    lot_number = str(raw.get("ln") or raw.get("lotNumberStr") or "")
    year = raw.get("lcy") or raw.get("y") or raw.get("yr")
    make = raw.get("mkn") or raw.get("mk")
    model = raw.get("lm") or raw.get("mdn") or raw.get("md")
    damage = raw.get("dd") or raw.get("dmg")
    features = raw.get("lfd") or []
    is_nlr = any("no license" in str(f).lower() for f in features)
    drive_status = raw.get("lcd") or ""
    has_keys_raw = raw.get("hk") or ""
    has_keys = has_keys_raw.upper() == "YES" if has_keys_raw else None

    return {
        "lot_number": lot_number,
        "title": raw.get("ld") or f"{year or ''} {make or ''} {model or ''}".strip(),
        "year": year,
        "make": make,
        "model": model,
        "trim": raw.get("ltd") or "",
        "damage": damage,
        "secondary_damage": raw.get("sdd") or raw.get("sd") or "",
        "drive_status": drive_status,
        "has_keys": has_keys,
        "odometer": raw.get("orr") or raw.get("od"),
        "sale_date": raw.get("ad"),
        "location": raw.get("yn"),
        "estimate": raw.get("la") or raw.get("lv"),
        "current_bid": raw.get("hb") or 0,
        "engine": raw.get("egn") or "",
        "vin": raw.get("fv") or "",
        "title_type": raw.get("tgd") or "",
        "image_url": raw.get("tims"),
        "is_nlr": is_nlr,
        "url": f"https://www.copart.com/lot/{lot_number}/{raw.get('ldu', '')}".rstrip("/"),
    }


def _wait_for_new_lots(intercepted, previous_count, timeout=8):
    start = time.time()
    while time.time() - start < timeout:
        if len(intercepted) > previous_count:
            return True
        time.sleep(0.5)
    return False


def search_playwright(makes, models, damage_types, year_min=None, year_max=None,
                      max_odometer=None, max_pages=10):
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
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage", "--window-size=1280,900",
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
            java_script_enabled=True,
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = { runtime: {} };
        """)
        page = context.new_page()

        def handle_response(response):
            try:
                if response.status != 200:
                    return
                if "application/json" not in response.headers.get("content-type", ""):
                    return
                if "search-results" not in response.url:
                    return
                data = response.json()
                content = (
                    data.get("data", {}).get("results", {}).get("content")
                    or data.get("returnObject", {}).get("results", {}).get("content")
                    or []
                )
                if content:
                    logger.info("Intercepted %d lots from: %s", len(content), response.url)
                    intercepted.extend(content)
            except Exception as e:
                logger.debug("Response intercept error: %s", e)

        page.on("response", handle_response)

        logger.info("Visiting Copart homepage for cookies...")
        try:
            page.goto("https://www.copart.com/", wait_until="domcontentloaded", timeout=20_000)
            time.sleep(2)
        except Exception as e:
            logger.warning("Homepage visit failed: %s", e)

        logger.info("Navigating to search: %s", url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except PWTimeout:
            logger.warning("domcontentloaded timeout — continuing anyway")

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass
        time.sleep(2)
        logger.info("Page 1: %d lots intercepted", len(intercepted))

        current_page = 1
        while current_page < max_pages:
            previous_count = len(intercepted)
            next_page_num = current_page + 1
            clicked = False
            try:
                result = page.evaluate(f"""
                    (() => {{
                        const btns = document.querySelectorAll('button.p-paginator-page');
                        for (const btn of btns) {{
                            if (btn.textContent.trim() === '{next_page_num}' && !btn.disabled) {{
                                btn.click();
                                return 'clicked:' + btn.className;
                            }}
                        }}
                        const next = document.querySelector('button.p-paginator-next:not([disabled])');
                        if (next) {{ next.click(); return 'clicked-next:' + next.className; }}
                        return 'not_found';
                    }})()
                """)
                if result and result.startswith("clicked"):
                    clicked = True
                    logger.info("Clicked page %d: %s", next_page_num, result)
                else:
                    logger.info("No more pages after page %d (%s)", current_page, result)
            except Exception as e:
                logger.debug("JS pagination error: %s", e)

            if not clicked:
                break

            got_new = _wait_for_new_lots(intercepted, previous_count, timeout=12)
            current_page += 1
            if got_new:
                logger.info("Page %d: %d new lots (total: %d)",
                            current_page, len(intercepted) - previous_count, len(intercepted))
            else:
                logger.info("Page %d: no new lots — stopping", current_page)
                break

        logger.info("Playwright: scraped %d page(s), intercepted %d total lots",
                    current_page, len(intercepted))
        browser.close()

    # Deduplicate
    seen_ln = set()
    unique = []
    for raw in intercepted:
        ln = str(raw.get("ln") or raw.get("lotNumberStr") or "")
        if ln and ln not in seen_ln:
            seen_ln.add(ln)
            unique.append(raw)
    logger.info("After dedup: %d unique lots", len(unique))

    # Apply all filters including MODEL
    filtered = [r for r in unique if _matches_filters(
        r, makes, models, damage_types, year_min, year_max, max_odometer)]
    logger.info("After client-side filter (incl. model): %d matched", len(filtered))

    results = [_parse_lot(raw) for raw in filtered]
    results = [r for r in results if r.get("lot_number")]
    logger.info("Playwright returned %d lots", len(results))
    return results
