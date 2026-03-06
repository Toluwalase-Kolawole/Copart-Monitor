"""
Playwright-based fallback scraper for Copart.
Used when the unofficial API fails or returns no results.
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.copart.com/lotSearchResults/"


def _build_search_url(makes: list[str], damage_types: list[str]) -> str:
    """Build Copart search URL with query parameters."""
    parts = []
    if makes:
        # Copart search uses 'make' param
        parts.append(f"make={','.join(m.upper() for m in makes)}")
    if damage_types:
        parts.append(f"primaryDamage={','.join(d.upper() for d in damage_types)}")
    query = "&".join(parts)
    return f"{SEARCH_URL}?{query}" if query else SEARCH_URL


def _parse_lot_from_row(row) -> Optional[dict]:
    """Extract lot data from a Playwright element handle."""
    try:
        lot_number = row.get_attribute("data-lot-number") or ""
        cells = row.query_selector_all("td")
        if len(cells) < 5:
            return None

        title = cells[1].inner_text().strip() if len(cells) > 1 else ""
        damage = cells[3].inner_text().strip() if len(cells) > 3 else ""
        location = cells[4].inner_text().strip() if len(cells) > 4 else ""
        sale_date = cells[5].inner_text().strip() if len(cells) > 5 else ""

        img_el = row.query_selector("img")
        image_url = img_el.get_attribute("src") if img_el else ""

        return {
            "lot_number": lot_number,
            "title": title,
            "year": None,
            "make": None,
            "model": None,
            "damage": damage,
            "odometer": None,
            "sale_date": sale_date,
            "location": location,
            "estimate": None,
            "image_url": image_url,
            "url": f"https://www.copart.com/lot/{lot_number}",
        }
    except Exception as e:
        logger.debug("Failed to parse row: %s", e)
        return None


def search_playwright(
    makes: list[str],
    damage_types: list[str],
    max_pages: int = 3,
) -> list[dict]:
    """
    Scrape Copart using Playwright (headless Chromium).
    Falls back logic when API fails.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError:
        raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")

    url = _build_search_url(makes, damage_types)
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        # Mask automation
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = context.new_page()
        logger.info("Playwright: navigating to %s", url)

        try:
            page.goto(url, wait_until="networkidle", timeout=60_000)
        except PlaywrightTimeout:
            logger.warning("Page load timed out, trying to scrape what loaded")

        # Handle cookie consent if present
        try:
            page.click("button:has-text('Accept')", timeout=3_000)
        except Exception:
            pass

        for current_page in range(max_pages):
            logger.info("Playwright: scraping page %d", current_page + 1)

            # Wait for lot rows
            try:
                page.wait_for_selector("tr[data-lot-number]", timeout=15_000)
            except PlaywrightTimeout:
                logger.warning("No lot rows found on page %d", current_page + 1)
                break

            rows = page.query_selector_all("tr[data-lot-number]")
            logger.info("Found %d rows on page %d", len(rows), current_page + 1)

            for row in rows:
                lot = _parse_lot_from_row(row)
                if lot and lot["lot_number"]:
                    results.append(lot)

            # Try to go to next page
            if current_page + 1 < max_pages:
                next_btn = page.query_selector("a[aria-label='Next page'], button[aria-label='Next page']")
                if not next_btn or not next_btn.is_enabled():
                    logger.info("No next page button, stopping pagination")
                    break
                next_btn.click()
                time.sleep(2)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass

        browser.close()

    logger.info("Playwright returned %d lots", len(results))
    return results
