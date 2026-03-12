"""
Copart Auction Tracker — Phase 2
Monitors active lots for bid price and auction close time.
Uses the authenticated /data/lotdetails/dynamic/{lot} endpoint.
Requires COPART_COOKIES secret to be set in GitHub Actions.
"""
import httpx
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Authenticated endpoint (requires session cookies) ──────────────────────
DYNAMIC_URL = "https://www.copart.com/data/lotdetails/dynamic/{lot_number}"
HOME_URL = "https://www.copart.com/"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.copart.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

DEFAULT_MAX_BID = 6000
TARGET_PRICES = {
    ("2027", "TOYOTA", "RAV4"): 7000,
    ("2026", "TOYOTA", "RAV4"): 7000,
    ("2025", "TOYOTA", "RAV4"): 7000,
    ("2024", "TOYOTA", "RAV4"): 7000,
    ("2023", "TOYOTA", "RAV4"): 7000,
    ("2022", "TOYOTA", "RAV4"): 7000,
}


def get_target_price(year, make, model):
    year = str(year or "")
    make = (make or "").upper()
    model = (model or "").upper()
    for (t_year, t_make, t_model), price in TARGET_PRICES.items():
        if t_year == year and t_make in make and t_model in model:
            return price
    return DEFAULT_MAX_BID


def _build_cookie_header():
    """
    Read COPART_COOKIES env var (full cookie string from browser DevTools)
    and return it ready to use in headers.
    Format: "usersessionid=abc123; C2BID=xyz; reese84=..."
    """
    cookies = os.environ.get("COPART_COOKIES", "").strip()
    if not cookies:
        logger.warning("COPART_COOKIES env var not set — bid fetch will fail auth")
    return cookies


def get_bid_details(client, lot_number):
    """Fetch live bid data from the authenticated dynamic endpoint."""
    url = DYNAMIC_URL.format(lot_number=lot_number)
    try:
        resp = client.get(url)
        if resp.status_code == 401 or resp.status_code == 403:
            logger.error("Auth failed for lot %s (HTTP %d) — check COPART_COOKIES secret", lot_number, resp.status_code)
            return None
        resp.raise_for_status()
        data = resp.json()
        details = (data.get("data") or {}).get("lotDetails") or {}
        if not details:
            logger.warning("Empty lotDetails for lot %s — response: %s", lot_number, str(data)[:200])
            return None
        return {
            "lot_number": lot_number,
            "current_bid": details.get("currentBid", 0),
            "lot_sold": details.get("lotSold", False),
            "auction_status": details.get("lotAuctionStatus", ""),
            "bid_status": details.get("bidStatus", ""),       # HIGH_BIDDER / OUTBID / NO_BID
            "reserve_met": details.get("sellerReserveMet", False),
            "bid_increment": details.get("bidIncrement", 25),
            "my_max_bid": details.get("maxBid", 0),
            "auction_id": details.get("auctionId"),
        }
    except Exception as e:
        logger.warning("bid fetch failed for lot %s: %s", lot_number, e)
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
                "last_bid_status": None,
                "alerted_closing": False,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "bid_history": [],
                "final_bid": None,
                "closed_at": None,
                "auction_result": None,
            }
            added += 1
    save_watchlist(watchlist, watchlist_file)
    logger.info("Watchlist: added %d new lots (total: %d)", added, len(watchlist))
    return watchlist


def _record_bid_snapshot(lot_entry, current_bid):
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bid": current_bid,
    }
    if "bid_history" not in lot_entry:
        lot_entry["bid_history"] = []
    if not lot_entry["bid_history"] or lot_entry["bid_history"][-1]["bid"] != current_bid:
        lot_entry["bid_history"].append(snapshot)



def sync_copart_watchlist(watchlist_file, cookie_str):
    """
    Pull lot IDs from Copart's native watchlist (the ❤️ button) and
    add any new ones to our local watchlist.json, auto-fetching their details.
    Endpoint: GET /data/lots/watchList
    Returns the number of new lots added.
    """
    url = "https://www.copart.com/data/lots/watchList"
    headers_auth = {**HEADERS, "Cookie": cookie_str} if cookie_str else HEADERS

    try:
        with httpx.Client(headers=headers_auth, timeout=20, follow_redirects=True) as client:
            client.get(HOME_URL)   # warm up session
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            lot_ids = [str(entry["lotId"]) for entry in (data.get("data") or {}).get("watchList") or []]
    except Exception as e:
        logger.warning("Could not fetch Copart watchlist: %s", e)
        return 0

    if not lot_ids:
        logger.info("Copart watchlist is empty")
        return 0

    existing = load_watchlist(watchlist_file)
    new_ids = [lid for lid in lot_ids if lid not in existing]

    if not new_ids:
        logger.info("Copart watchlist sync: all %d lot(s) already tracked", len(lot_ids))
        return 0

    logger.info("Copart watchlist sync: fetching details for %d new lot(s): %s", len(new_ids), new_ids)

    # Fetch solr metadata for each new lot
    lots_to_add = []
    with httpx.Client(headers=headers_auth, timeout=20, follow_redirects=True) as client:
        for lid in new_ids:
            try:
                r = client.get(f"https://www.copart.com/public/data/lotdetails/solr/{lid}")
                raw = ((r.json().get("data") or {}).get("lotDetails") or {})
                if not raw:
                    logger.warning("No solr data for watchlisted lot %s", lid)
                    continue
                lots_to_add.append({
                    "lot_number": str(lid),
                    "title": f"{raw.get('lcy', '')} {raw.get('mk', '')} {raw.get('lm', '')} {raw.get('ltrim', '')}".strip(),
                    "year":  raw.get("lcy"),
                    "make":  raw.get("mk"),
                    "model": raw.get("lm"),
                    "trim":  raw.get("ltrim", ""),
                    "damage": raw.get("dmg", ""),
                    "drive_status": raw.get("drv", ""),
                    "has_keys": raw.get("hk"),
                    "odometer": raw.get("od"),
                    "location": raw.get("yn", ""),
                    "estimate": raw.get("est"),
                    "sale_date": raw.get("ad"),
                    "is_nlr": raw.get("bnm", "").upper() == "NLR",
                    "url": f"https://www.copart.com/lot/{lid}",
                    "image_url": raw.get("thmb", ""),
                })
            except Exception as e:
                logger.warning("Failed to fetch details for lot %s: %s", lid, e)

    if lots_to_add:
        add_to_watchlist(lots_to_add, watchlist_file)
        logger.info("Copart watchlist sync: added %d new lot(s) to watchlist.json", len(lots_to_add))

    return len(lots_to_add)


def check_watchlist(watchlist_file, notifier_fn):
    # Build auth headers with session cookies
    cookie_str = _build_cookie_header()

    # Sync Copart's native ❤️ watchlist first — auto-adds any new lot you've hearted
    synced = sync_copart_watchlist(watchlist_file, cookie_str)
    if synced:
        logger.info("Pulled %d new lot(s) from your Copart watchlist", synced)

    watchlist = load_watchlist(watchlist_file)
    if not watchlist:
        logger.info("Watchlist is empty")
        return
    headers = {**HEADERS, "Cookie": cookie_str} if cookie_str else HEADERS
    now = datetime.now(timezone.utc)
    to_close = []
    updated = 0

    with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client:
        # Warm up session
        client.get(HOME_URL)

        for ln, lot in watchlist.items():
            bid = get_bid_details(client, ln)
            if not bid:
                logger.warning("Skipping lot %s — could not fetch bid data", ln)
                continue

            current_bid = bid["current_bid"]
            status = bid["auction_status"]
            bid_status = bid["bid_status"]   # HIGH_BIDDER / OUTBID / NO_BID
            sold = bid["lot_sold"]
            target = lot["target_price"]
            prev_bid = lot.get("last_bid")
            prev_bid_status = lot.get("last_bid_status")

            # Always record snapshot
            _record_bid_snapshot(lot, current_bid)

            # Time to close
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

            # ── Closed/sold ────────────────────────────────────────────────
            if sold or status in ("ENDED", "CLOSED", "SOLD"):
                logger.info("LOT %s CLOSED — final bid: $%s", ln, current_bid)
                lot["final_bid"] = current_bid
                lot["closed_at"] = now.isoformat()
                lot["auction_result"] = status if status else ("SOLD" if sold else "CLOSED")
                _record_bid_snapshot(lot, current_bid)
                notifier_fn(lot, "sold", current_bid=current_bid)
                to_close.append(ln)
                continue

            # ── Determine if we should alert ──────────────────────────────
            bid_changed = current_bid != prev_bid
            status_changed = bid_status != prev_bid_status  # e.g. became OUTBID

            # Closing soon alert (once only, within 10 mins)
            closing_soon = (
                minutes_until_close is not None
                and 0 < minutes_until_close <= 10
                and not lot.get("alerted_closing")
            )

            if closing_soon:
                lot["alerted_closing"] = True
                notifier_fn(lot, "closing_soon", current_bid=current_bid,
                            minutes_left=minutes_until_close,
                            bid_status=bid_status)
                lot["last_bid"] = current_bid
                lot["last_bid_status"] = bid_status
                updated += 1
                logger.info("LOT %s CLOSING SOON — %s mins | bid=$%s", ln,
                            f"{minutes_until_close:.0f}", current_bid)

            elif (bid_changed or status_changed) and current_bid <= target:
                # Only alert when bid is still under target
                logger.info("LOT %s BID UPDATE | $%s→$%s | %s | target=$%s",
                            ln, prev_bid, current_bid, bid_status, target)
                notifier_fn(lot, "update", current_bid=current_bid,
                            minutes_left=minutes_until_close,
                            bid_status=bid_status,
                            prev_bid=prev_bid)
                lot["last_bid"] = current_bid
                lot["last_bid_status"] = bid_status
                updated += 1

            elif bid_changed or status_changed:
                # Bid changed but over target — log only, no alert
                logger.info("LOT %s OVER TARGET | bid=$%s target=$%s status=%s",
                            ln, current_bid, target, bid_status)
                lot["last_bid"] = current_bid
                lot["last_bid_status"] = bid_status
            else:
                logger.info("LOT %s no change | bid=$%s status=%s", ln, current_bid, bid_status)

    # Archive closed lots
    archive_file = Path(watchlist_file).parent / "watchlist_archive.json"
    archive = {}
    if archive_file.exists():
        try:
            archive = json.loads(archive_file.read_text())
        except Exception:
            pass

    for ln in to_close:
        archive[ln] = watchlist.pop(ln)

    if to_close:
        archive_file.write_text(json.dumps(archive, indent=2))

    save_watchlist(watchlist, watchlist_file)
    logger.info("Watchlist check done: %d active, %d alerts sent, %d closed",
                len(watchlist), updated, len(to_close))
