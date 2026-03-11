"""
Copart Auction Tracker — Phase 2
Monitors active lots for bid price and auction close time.
Stores bid snapshots at every run and final bids for winning bid analysis.
"""
import httpx
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BID_URL = "https://www.copart.com/public/lots/bidDetails"
HOME_URL = "https://www.copart.com/"

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

DEFAULT_MAX_BID = 6000
TARGET_PRICES = {
    ("2027", "TOYOTA", "RAV4"): 6000,
    ("2026", "TOYOTA", "RAV4"): 6000,
    ("2025", "TOYOTA", "RAV4"): 6000,
    ("2024", "TOYOTA", "RAV4"): 6000,
    ("2023", "TOYOTA", "RAV4"): 6000,
}


def get_target_price(year, make, model):
    year = str(year or "")
    make = (make or "").upper()
    model = (model or "").upper()
    for (t_year, t_make, t_model), price in TARGET_PRICES.items():
        if t_year == year and t_make in make and t_model in model:
            return price
    return DEFAULT_MAX_BID


def get_bid_details(client, lot_number):
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
    p = Path(watchlist_file)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_watchlist(watchlist, watchlist_file):
    Path(watchlist_file).write_text(json.dumps(watchlist, indent=2))


def add_to_watchlist(lots, watchlist_file):
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
                "trim": lot.get("trim", ""),
                "damage": lot.get("damage"),
                "drive_status": lot.get("drive_status", ""),
                "has_keys": lot.get("has_keys"),
                "odometer": lot.get("odometer"),
                "location": lot.get("location", ""),
                "estimate": lot.get("estimate"),
                "url": lot.get("url", ""),
                "image_url": lot.get("image_url", ""),
                "sale_date": lot.get("sale_date"),
                "is_nlr": lot.get("is_nlr", False),
                "target_price": target,
                "last_bid": None,
                "alerted_closing": False,
                "added_at": datetime.now(timezone.utc).isoformat(),
                # Bid history — list of {timestamp, bid} snapshots
                "bid_history": [],
                # Final bid recorded when auction closes
                "final_bid": None,
                "closed_at": None,
                "auction_result": None,   # "SOLD" | "ENDED" | "CLOSED"
            }
            added += 1
    save_watchlist(watchlist, watchlist_file)
    logger.info("Watchlist: added %d new lots (total: %d)", added, len(watchlist))
    return watchlist


def _record_bid_snapshot(lot_entry, current_bid):
    """Append a timestamped bid snapshot to the lot's history."""
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bid": current_bid,
    }
    if "bid_history" not in lot_entry:
        lot_entry["bid_history"] = []
    # Only record if bid changed or no snapshots yet
    if not lot_entry["bid_history"] or lot_entry["bid_history"][-1]["bid"] != current_bid:
        lot_entry["bid_history"].append(snapshot)


def check_watchlist(watchlist_file, notifier_fn):
    watchlist = load_watchlist(watchlist_file)
    if not watchlist:
        logger.info("Watchlist is empty")
        return

    now = datetime.now(timezone.utc)
    to_close = []
    updated = 0

    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        client.get(HOME_URL)

        for ln, lot in watchlist.items():
            bid = get_bid_details(client, ln)
            if not bid:
                continue

            current_bid = bid["current_bid"]
            status = bid["auction_status"]
            sold = bid["lot_sold"]
            target = lot["target_price"]

            # Always record snapshot at every run
            _record_bid_snapshot(lot, current_bid)

            # Compute time to close
            sale_date = lot.get("sale_date")
            minutes_until_close = None
            if sale_date:
                try:
                    ts = int(sale_date)
                    if ts > 1_000_000_000_000:
                        ts = ts / 1000
                    close_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                    minutes_until_close = (close_time - now).total_seconds() / 60
                except Exception:
                    pass

            # Handle closed/sold lots
            if sold or status in ("ENDED", "CLOSED", "SOLD"):
                logger.info("LOT %s CLOSED — final bid: $%s", ln, current_bid)
                # Record final bid
                lot["final_bid"] = current_bid
                lot["closed_at"] = now.isoformat()
                lot["auction_result"] = status if status else ("SOLD" if sold else "CLOSED")
                # Final snapshot
                _record_bid_snapshot(lot, current_bid)
                notifier_fn(lot, "sold", current_bid=current_bid)
                to_close.append(ln)
                continue

            # Alert if under target
            if current_bid <= target:
                alert_type = "update"
                if minutes_until_close is not None and 0 < minutes_until_close <= 10:
                    if not lot.get("alerted_closing"):
                        alert_type = "closing_soon"
                        lot["alerted_closing"] = True

                if current_bid != lot.get("last_bid") or alert_type == "closing_soon":
                    logger.info("LOT %s %s | bid=$%s target=$%s mins_left=%s",
                                ln, lot.get("title", ""), current_bid, target,
                                f"{minutes_until_close:.0f}" if minutes_until_close else "?")
                    notifier_fn(lot, alert_type, current_bid=current_bid,
                                minutes_left=minutes_until_close)
                    lot["last_bid"] = current_bid
                    updated += 1
            else:
                logger.info("LOT %s bid=$%s OVER target=$%s", ln, current_bid, target)

    # Move closed lots to archive instead of deleting them
    archive_file = Path(watchlist_file).parent / "watchlist_archive.json"
    archive = {}
    if archive_file.exists():
        try:
            archive = json.loads(archive_file.read_text())
        except Exception:
            pass

    for ln in to_close:
        archive[ln] = watchlist.pop(ln)

    archive_file.write_text(json.dumps(archive, indent=2))
    save_watchlist(watchlist, watchlist_file)
    logger.info("Watchlist check: %d active, %d alerts, %d closed (archived)",
                len(watchlist), updated, len(to_close))
