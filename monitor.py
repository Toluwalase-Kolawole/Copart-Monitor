#!/usr/bin/env python3
"""
Copart Monitor — main entry point.

Usage:
  python monitor.py                    # Run with config from environment
  python monitor.py --test-telegram    # Send a test Telegram message
  python monitor.py --dry-run          # Fetch lots but don't notify or save state
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("copart_monitor")

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent / "src"))

from copart_api import search_api
from copart_playwright import search_playwright
from notifier import send_telegram, test_connection
from state_manager import load_state, save_state, find_new_lots, mark_seen

# ---------------------------------------------------------------------------
# Config from environment variables
# ---------------------------------------------------------------------------
def get_config():
    required = {
        "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID"),
    }

    for key, val in required.items():
        if not val:
            logger.error("Missing required environment variable: %s", key)
            sys.exit(1)

    makes_raw = os.environ.get("COPART_MAKES", "")
    damage_raw = os.environ.get("COPART_DAMAGE_TYPES", "")
    year_min_raw = os.environ.get("COPART_YEAR_MIN", "").strip()
    year_max_raw = os.environ.get("COPART_YEAR_MAX", "").strip()

    makes = [m.strip() for m in makes_raw.split(",") if m.strip()]
    damage_types = [d.strip() for d in damage_raw.split(",") if d.strip()]

    year_min = int(year_min_raw) if year_min_raw.isdigit() else None
    year_max = int(year_max_raw) if year_max_raw.isdigit() else None

    if not makes and not damage_types:
        logger.warning("No COPART_MAKES or COPART_DAMAGE_TYPES set — will fetch ALL listings")

    if year_min or year_max:
        logger.info("Year filter: %s – %s", year_min or "any", year_max or "any")

    return {
        "telegram_token": required["TELEGRAM_BOT_TOKEN"],
        "telegram_chat_id": required["TELEGRAM_CHAT_ID"],
        "makes": makes,
        "damage_types": damage_types,
        "year_min": year_min,
        "year_max": year_max,
        "max_pages": int(os.environ.get("COPART_MAX_PAGES", "3")),
        "state_file": Path(os.environ.get("STATE_FILE", "state.json")),
    }


# ---------------------------------------------------------------------------
# Scraping with API-first, Playwright fallback
# ---------------------------------------------------------------------------
def fetch_lots(makes, damage_types, year_min, year_max, max_pages):
    """Try API first; fall back to Playwright on failure or empty results."""
    logger.info(
        "Attempting Copart API... makes=%s damage=%s years=%s-%s",
        makes, damage_types, year_min or "*", year_max or "*",
    )
    try:
        lots = search_api(makes, damage_types, year_min=year_min, year_max=year_max, max_pages=max_pages)
        if lots:
            logger.info("✅ API succeeded with %d lots", len(lots))
            return lots
        else:
            logger.warning("API returned 0 results — falling back to Playwright")
    except Exception as e:
        logger.warning("API failed (%s) — falling back to Playwright", e)

    logger.info("Attempting Playwright scraper...")
    try:
        # Playwright fetches all results then we filter by year client-side
        lots = search_playwright(makes, damage_types, year_min=year_min, year_max=year_max, max_pages=max_pages)
        # Apply year filter post-fetch
        if year_min or year_max:
            before = len(lots)
            lots = [
                lot for lot in lots
                if _year_in_range(lot.get("year"), year_min, year_max)
            ]
            logger.info("Year filter %s-%s: %d → %d lots", year_min or "*", year_max or "*", before, len(lots))
        logger.info("✅ Playwright succeeded with %d lots", len(lots))
        return lots
    except Exception as e:
        logger.error("Playwright also failed: %s", e)
        return []


def _year_in_range(year, year_min, year_max):
    """Return True if year is within the specified range."""
    if year is None:
        return True  # Don't exclude lots with unknown year
    try:
        y = int(year)
        if year_min and y < year_min:
            return False
        if year_max and y > year_max:
            return False
        return True
    except (ValueError, TypeError):
        return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Copart listing monitor")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test Telegram message and exit")
    parser.add_argument("--dry-run", action="store_true", help="Fetch lots but don't notify or update state")
    args = parser.parse_args()

    config = get_config()

    # --- Test Telegram connection ---
    if args.test_telegram:
        logger.info("Sending test Telegram message...")
        ok = test_connection(config["telegram_token"], config["telegram_chat_id"])
        if ok:
            logger.info("✅ Telegram test message sent successfully")
            sys.exit(0)
        else:
            logger.error("❌ Telegram test failed — check your token and chat_id")
            sys.exit(1)

    # --- Load state ---
    state = load_state(config["state_file"])

    # --- Fetch lots ---
    lots = fetch_lots(
        config["makes"],
        config["damage_types"],
        config["year_min"],
        config["year_max"],
        config["max_pages"],
    )

    if not lots:
        logger.warning("No lots fetched from any source — exiting")
        if not args.dry_run:
            save_state(state, config["state_file"])
        sys.exit(0)

    # --- Find new lots ---
    new_lots = find_new_lots(lots, state)

    if not new_lots:
        logger.info("No new lots found this run")
    else:
        logger.info("🆕 Found %d new lot(s)!", len(new_lots))

        if args.dry_run:
            logger.info("[DRY RUN] Would notify about these lots:")
            for lot in new_lots:
                logger.info("  • [%s] %s — %s", lot["lot_number"], lot["title"], lot["url"])
        else:
            send_telegram(
                token=config["telegram_token"],
                chat_id=config["telegram_chat_id"],
                lots=new_lots,
            )

    # --- Update & save state ---
    if not args.dry_run:
        state = mark_seen(new_lots, state)
        save_state(state, config["state_file"])
        logger.info("State saved.")
    else:
        logger.info("[DRY RUN] State not saved")


if __name__ == "__main__":
    main()
