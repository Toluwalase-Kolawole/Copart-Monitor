#!/usr/bin/env python3
"""
Copart Monitor — main entry point.

GitHub Variables you can change any time without touching code:
  COPART_MAKES          e.g. Toyota,Hyundai
  COPART_MODELS         e.g. RAV4,RAV4 HYBRID,RAV4 PRIME,RAV4 ADVENTURE,RAV4 XSE,RAV4 PLUG-IN HYBRID
  COPART_DAMAGE_TYPES   e.g. REAR END,SIDE,HAIL,MINOR DENT/SCRATCHES,NORMAL WEAR,VANDALISM
  COPART_YEAR_MIN       e.g. 2023
  COPART_YEAR_MAX       e.g. 2027
  COPART_MAX_ODOMETER   e.g. 40000
  COPART_MAX_PAGES      e.g. 11

Usage:
  python monitor.py             # Run with config from environment
  python monitor.py --test-telegram   # Send a test Telegram message
  python monitor.py --dry-run         # Fetch lots but don't notify or save state
"""
import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("copart_monitor")

sys.path.insert(0, str(Path(__file__).parent))

from copart_api import search_api
from copart_playwright import search_playwright
from notifier import send_telegram, test_connection
from state_manager import load_state, save_state, find_new_lots, mark_seen


def get_config():
    required = {
        "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID"),
    }
    for key, val in required.items():
        if not val:
            logger.error("Missing required environment variable: %s", key)
            sys.exit(1)

    makes_raw   = os.environ.get("COPART_MAKES", "")
    models_raw  = os.environ.get("COPART_MODELS", "")
    damage_raw  = os.environ.get("COPART_DAMAGE_TYPES", "")
    year_min_raw = os.environ.get("COPART_YEAR_MIN", "").strip()
    year_max_raw = os.environ.get("COPART_YEAR_MAX", "").strip()

    makes        = [m.strip() for m in makes_raw.split(",")  if m.strip()]
    models       = [m.strip() for m in models_raw.split(",") if m.strip()]
    damage_types = [d.strip() for d in damage_raw.split(",") if d.strip()]
    year_min     = int(year_min_raw) if year_min_raw.isdigit() else None
    year_max     = int(year_max_raw) if year_max_raw.isdigit() else None

    max_odo_raw  = os.environ.get("COPART_MAX_ODOMETER", "").strip().replace(",", "")
    max_odometer = int(max_odo_raw) if max_odo_raw.isdigit() else None

    logger.info("Config → makes=%s models=%s years=%s-%s odo≤%s",
                makes, models, year_min or "*", year_max or "*", max_odometer or "*")

    return {
        "telegram_token":  required["TELEGRAM_BOT_TOKEN"],
        "telegram_chat_id": required["TELEGRAM_CHAT_ID"],
        "makes":        makes,
        "models":       models,
        "damage_types": damage_types,
        "year_min":     year_min,
        "year_max":     year_max,
        "max_odometer": max_odometer,
        "max_pages":    int(os.environ.get("COPART_MAX_PAGES", "3")),
        "state_file":   Path(os.environ.get("STATE_FILE", "state.json")),
    }


def fetch_lots(makes, models, damage_types, year_min, year_max, max_odometer, max_pages):
    """Try API first; fall back to Playwright on failure or empty results.
    Both paths apply the same model filter — no leakage."""
    logger.info("Fetching: makes=%s models=%s damage=%s years=%s-%s odo≤%s",
                makes, models, damage_types, year_min or "*", year_max or "*", max_odometer or "*")
    try:
        lots = search_api(
            makes, models, damage_types,
            year_min=year_min, year_max=year_max,
            max_odometer=max_odometer, max_pages=max_pages
        )
        if lots:
            logger.info("✅ API succeeded with %d lots", len(lots))
            return lots
        else:
            logger.warning("API returned 0 results — falling back to Playwright")
    except Exception as e:
        logger.warning("API failed (%s) — falling back to Playwright", e)

    logger.info("Attempting Playwright scraper...")
    try:
        # Pass models so the Playwright path also filters by model
        lots = search_playwright(
            makes, models, damage_types,
            year_min=year_min, year_max=year_max,
            max_odometer=max_odometer, max_pages=max_pages
        )
        logger.info("✅ Playwright succeeded with %d lots", len(lots))
        return lots
    except Exception as e:
        logger.error("Playwright also failed: %s", e)
        return []


def main():
    parser = argparse.ArgumentParser(description="Copart listing monitor")
    parser.add_argument("--test-telegram", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = get_config()

    if args.test_telegram:
        ok = test_connection(config["telegram_token"], config["telegram_chat_id"])
        sys.exit(0 if ok else 1)

    state = load_state(config["state_file"])
    lots  = fetch_lots(
        config["makes"], config["models"], config["damage_types"],
        config["year_min"], config["year_max"],
        config["max_odometer"], config["max_pages"],
    )

    if not lots:
        logger.warning("No lots fetched — exiting")
        if not args.dry_run:
            save_state(state, config["state_file"])
        sys.exit(0)

    new_lots    = find_new_lots(lots, state)
    is_first_run = state.get("last_run") is None

    if is_first_run:
        logger.info("First run — saving %d lots as baseline (no notifications)", len(lots))
    elif not new_lots:
        logger.info("No new lots found this run")
    else:
        logger.info("🆕 Found %d new lot(s)!", len(new_lots))
        if args.dry_run:
            for lot in new_lots:
                nlr = "✅ NLR" if lot.get("is_nlr") else "🔑 Broker"
                logger.info("  • [%s] %s %s — %s",
                            lot["lot_number"], lot["title"], nlr, lot["url"])
        else:
            MAX_NOTIFY = 20
            notify_lots = new_lots[:MAX_NOTIFY]
            send_telegram(
                token=config["telegram_token"],
                chat_id=config["telegram_chat_id"],
                lots=notify_lots,
            )
            if len(new_lots) > MAX_NOTIFY:
                logger.info("Capped at %d notifications — %d more saved silently",
                            MAX_NOTIFY, len(new_lots) - MAX_NOTIFY)

    if not args.dry_run:
        lots_to_track = lots if is_first_run else new_lots
        if lots_to_track:
            from auction_tracker import add_to_watchlist
            add_to_watchlist(lots_to_track, Path("watchlist.json"))
            logger.info("Watchlist updated with %d lot(s)", len(lots_to_track))
        # Always mark seen but only commit watchlist when it actually changed
        state = mark_seen(lots if is_first_run else new_lots, state)
        save_state(state, config["state_file"])
        logger.info("State saved.")
    else:
        logger.info("[DRY RUN] State not saved")


if __name__ == "__main__":
    main()


def run_watchlist_check(config):
    """Called by auction_tracker to check active bid updates."""
    from auction_tracker import check_watchlist
    from notifier import send_bid_alert

    watchlist_file = config.get("watchlist_file", Path("watchlist.json"))

    def notifier(lot, alert_type, current_bid=0, minutes_left=None):
        send_bid_alert(
            token=config["telegram_token"],
            chat_id=config["telegram_chat_id"],
            lot=lot,
            alert_type=alert_type,
            current_bid=current_bid,
            minutes_left=minutes_left,
        )

    check_watchlist(watchlist_file, notifier)
