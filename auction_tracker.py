"""
Copart Auction Tracker — Phase 2
Monitors active lots for bid price and auction close time.
Sends Telegram alerts when:
- Current bid is under target price
- Auction closing in 10 minutes and bid still under target
"""

import httpx
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BID_URL    = "https://www.copart.com/public/lots/bidDetails"
LOT_URL    = "https://www.copart.com/public/lots/search-results"
HOME_URL   = "https://www.copart.com/"

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

# Target prices per model — blanket default if not matched
DEFAULT_MAX_BID = 6000

TARGET_PRICES = {
    # (year, make, model_contains) -> max bid in USD
    ("2027", "TOYOTA", "RAV4"): 6000,
    ("2026", "TOYOTA", "RAV4"): 6000,
    ("2025", "TOYOTA", "RAV4"): 6000,
    ("2024", "TOYOTA", "RAV4"): 6000,
    ("2023", "TOYOTA", "RAV4"): 6000,
}


def get_target_price(year, make, model):
    """Return target max bid for a given vehicle."""
    year = str(year or "")
    make = (make or "").upper()
    model = (model or "").upper()
    for (t_year, t_make, t_model), price in TARGET_PRICES.items():
        if t_year == year and t_make in make and t_model in model:
            return price
    return DEFAULT_MAX_BID


def get_bid_details(client, lot_number):
    """Fetch current bid details for a lot."""
    try:
        resp = client.post(BID_URL, json={"lotNumber": int(lot_number)})
        resp.raise_for_status()
        data = resp.json()
        result = data.get("data") or data.get("returnObject") or {}
        return {
            "lot_number": lot_number,
            "current_bid": result.get("currentBid", 0),
            "lot_sold": result.get("lotSold", False),
            "auction_status": result.get("lotAuctionStatus", ""),
            "reserve_met": result.get("sellerReserveMet", False),
            "high_bidder": result.get("highBidder"),
        }
    except Exception as e:
        logger.warning("bid fetch failed for %s: %s", lot_number, e)
        return None


def load_watchlist(watchlist_file):
    """Load tracked lots from watchlist JSON file."""
    p = Path(watchlist_file)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_watchlist(watchlist, watchlist_file):
    """Save watchlist to JSON file."""
    Path(watchlist_file).write_text(json.dumps(watchlist, indent=2))


def add_to_watchlist(lots, watchlist_file):
    """Add newly discovered lots to the watchlist."""
    watchlist = load_watchlist(watchlist_file)
    added = 0
    for lot in lots:
        ln = lot["lot_number"]
        if ln not in watchlist:
            target = get_target_price(lot.get("year"), lot.get("make"), lot.get("model"))
            watchlist[ln] = {
                "lot_number": ln,
                "title": lot.get("title", ""),
                "year": lot.get("year"),
                "make": lot.get("make"),
                "model": lot.get("model"),
                "damage": lot.get("damage"),
                "odometer": lot.get("odometer"),
                "url": lot.get("url", ""),
                "image_url": lot.get("image_url", ""),
                "sale_date": lot.get("sale_date"),
                "target_price": target,
                "last_bid": None,
                "alerted_closing": False,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            added += 1
    save_watchlist(watchlist, watchlist_file)
    logger.info("Watchlist: added %d new lots (total: %d)", added, len(watchlist))
    return watchlist


def check_watchlist(watchlist_file, notifier_fn):
    """
    Check all active watchlist lots.
    Calls notifier_fn(lot_info, alert_type) for lots under target price.
    alert_type: 'update' | 'closing_soon' | 'sold'
    """
    watchlist = load_watchlist(watchlist_file)
    if not watchlist:
        logger.info("Watchlist is empty")
        return

    now = datetime.now(timezone.utc)
    to_remove = []
    updated = 0

    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        client.get(HOME_URL)  # get cookies

        for ln, lot in watchlist.items():
            bid = get_bid_details(client, ln)
            if not bid:
                continue

            current_bid = bid["current_bid"]
            status = bid["auction_status"]
            sold = bid["lot_sold"]
            target = lot["target_price"]

            # Parse auction close time from Unix ms timestamp (field: ad)
            sale_date = lot.get("sale_date")
            minutes_until_close = None
            if sale_date:
                try:
                    ts = int(sale_date)
                    # Copart returns Unix ms timestamps
                    if ts > 1_000_000_000_000:
                        ts = ts / 1000
                    close_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                    minutes_until_close = (close_time - now).total_seconds() / 60
                except Exception:
                    pass

            # Remove sold or ended lots
            if sold or status in ("ENDED", "CLOSED", "SOLD"):
                logger.info("LOT %s CLOSED — final bid: $%s", ln, current_bid)
                notifier_fn(lot, "sold", current_bid=current_bid)
                to_remove.append(ln)
                continue

            # Only alert if current bid is under target
            if current_bid <= target:
                alert_type = "update"

                # Urgent alert if closing within 10 minutes
                if minutes_until_close is not None and 0 < minutes_until_close <= 10:
                    if not lot.get("alerted_closing"):
                        alert_type = "closing_soon"
                        lot["alerted_closing"] = True

                # Only notify if bid changed
                if current_bid != lot.get("last_bid") or alert_type == "closing_soon":
                    logger.info("LOT %s %s | bid=$%s target=$%s mins_left=%s",
                                ln, lot.get("title", ""), current_bid, target,
                                f"{minutes_until_close:.0f}" if minutes_until_close else "?")
                    notifier_fn(lot, alert_type,
                                current_bid=current_bid,
                                minutes_left=minutes_until_close)
                    lot["last_bid"] = current_bid
                    updated += 1
            else:
                logger.info("LOT %s bid=$%s OVER target=$%s — skipping",
                            ln, current_bid, target)

    # Remove closed lots
    for ln in to_remove:
        del watchlist[ln]

    save_watchlist(watchlist, watchlist_file)
    logger.info("Watchlist check complete: %d lots active, %d alerts sent, %d removed",
                len(watchlist), updated, len(to_remove))
