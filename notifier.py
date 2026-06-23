"""
Telegram notifier for Copart Monitor.
NLR lots get a ✅ badge. Broker lots get a 🔑 badge.
Drive status (RUNS AND DRIVES / STATIONARY etc.) and keys shown on every alert.
"""
import logging
import httpx
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _esc(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    for ch in r"\_*[]()~`>#+=|{}.!-":
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_sale_date(ts_ms) -> str:
    if not ts_ms:
        return "TBD"
    try:
        ts = int(ts_ms)
        if ts > 1_000_000_000_000:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%b %d, %Y %I:%M %p UTC")
    except Exception:
        return str(ts_ms)


def _drive_status_line(lot) -> str:
    """Format the drive status + keys line."""
    drive = (lot.get("drive_status") or "").strip().upper()
    has_keys = lot.get("has_keys")

    # Drive status emoji
    if "RUNS AND DRIVES" in drive or "RUN AND DRIVE" in drive:
        drive_emoji = "🟢"
    elif "STATIONARY" in drive:
        drive_emoji = "🔴"
    elif "ENHANCED" in drive:
        drive_emoji = "🟡"
    elif drive:
        drive_emoji = "⚪"
    else:
        return ""  # No status available

    drive_str = drive.title() if drive else "Unknown"

    # Keys indicator
    if has_keys is True:
        keys_str = "🔑 Keys: Yes"
    elif has_keys is False:
        keys_str = "🚫 Keys: No"
    else:
        keys_str = ""

    parts = [f"{drive_emoji} {_esc(drive_str)}"]
    if keys_str:
        parts.append(_esc(keys_str))
    return " \\| ".join(parts)


def send_telegram(token: str, chat_id: str, lots: list):
    with httpx.Client(timeout=20) as client:
        for lot in lots:
            try:
                _send_lot(client, token, chat_id, lot)
            except Exception as e:
                logger.warning("Failed to send lot %s: %s", lot.get("lot_number"), e)


def _send_lot(client, token, chat_id, lot):
    is_nlr = lot.get("is_nlr", False)
    nlr_line = "✅ *No License Required*" if is_nlr else "🔑 *Broker Required*"

    title = lot.get("title", "Unknown")
    lot_num = lot.get("lot_number", "")
    damage = lot.get("damage") or "N/A"
    sec_dmg = lot.get("secondary_damage") or ""
    odo = lot.get("odometer")
    estimate = lot.get("estimate")
    location = lot.get("location") or "Unknown"
    sale_date = _format_sale_date(lot.get("sale_date"))
    url = lot.get("url", "")
    image_url = lot.get("image_url", "")

    odo_str = f"{int(odo):,} mi" if odo else "N/A"
    est_str = f"\\${int(estimate):,}" if estimate else "N/A"
    dmg_str = _esc(damage)
    if sec_dmg:
        dmg_str += f" \\| {_esc(sec_dmg)}"

    # Drive status line
    status_line = _drive_status_line(lot)

    lines = [
        f"🚗 *{_esc(title)}*",
        nlr_line,
        "",
        f"📅 Sale: {_esc(sale_date)}",
        f"📍 {_esc(location)}",
        f"🔧 Damage: {dmg_str}",
    ]
    if status_line:
        lines.append(f"🚦 Status: {status_line}")
    lines += [
        f"🛣 Odometer: {_esc(odo_str)}",
        f"💲 Est\\. Value: {est_str}",
        f"🔢 Lot: {_esc(lot_num)}",
        "",
        f"[View on Copart]({url})",
    ]

    text = "\n".join(lines)

    if image_url:
        resp = client.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            json={"chat_id": chat_id, "photo": image_url, "caption": text, "parse_mode": "MarkdownV2"},
        )
    else:
        resp = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"},
        )

    if resp.status_code != 200:
        logger.warning("Telegram error %d: %s", resp.status_code, resp.text[:200])
    else:
        logger.info("Sent lot %s (%s)", lot_num, "NLR" if is_nlr else "Broker")


def test_connection(token: str, chat_id: str) -> bool:
    text = (
        "✅ *Copart Monitor connected\\!*\n\n"
        "You will now receive alerts for new listings\\.\n\n"
        "🔑 Lots needing a broker will show: 🔑 *Broker Required*\n"
        "✅ Lots you can buy directly: ✅ *No License Required*\n"
        "🚦 Drive status \\(Runs and Drives / Stationary\\) shown on every alert"
    )
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"},
            )
        ok = resp.status_code == 200
        if ok:
            logger.info("✅ Telegram test OK")
        else:
            logger.error("Telegram test failed: %d %s", resp.status_code, resp.text[:200])
        return ok
    except Exception as e:
        logger.error("Telegram test exception: %s", e)
        return False


# ---------------------------------------------------------------------------
# Auction tracker bid alerts
# ---------------------------------------------------------------------------

ALERT_EMOJIS = {
    "closing_soon": "🚨",
    "sold": "🔴",
    "update": "🟢",
    "over_budget": "⚠️",
    "gone": "🗑",
}


def send_cookie_expired_alert(token: str, chat_id: str):
    """Send a one-time Telegram alert when COPART_COOKIES auth has expired."""
    text = (
        "⚠️ *COPART\\_COOKIES have expired\\!*\n\n"
        "Bid tracking is paused because Copart rejected the session cookies\\.\n\n"
        "To resume tracking:\n"
        "1\\. Log in to copart\\.com in your browser\n"
        "2\\. Copy the full Cookie header from DevTools → Network\n"
        "3\\. Update the *COPART\\_COOKIES* secret in your GitHub repository settings"
    )
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"},
            )
        if resp.status_code != 200:
            logger.warning("Cookie-expired alert failed: %d %s", resp.status_code, resp.text[:200])
        else:
            logger.info("Sent cookie-expired alert")
    except Exception as exc:
        logger.warning("Cookie-expired alert exception: %s", exc)


def send_daily_digest(token: str, chat_id: str, watchlist: dict, archive: dict):
    """
    Send a daily summary message to Telegram with:
    - Count of active lots vs. over-budget lots
    - Lots closing within 24 hours
    - Archive stats (total closed auctions)
    """
    now = datetime.now(timezone.utc)
    active_lots = list(watchlist.values())
    total_active = len(active_lots)
    total_closed = len(archive)

    under_budget = sum(
        1 for lot in active_lots
        if lot.get("last_bid") is not None
        and lot["last_bid"] <= lot.get("target_price", 0)
    )
    over_budget = sum(
        1 for lot in active_lots
        if lot.get("last_bid") is not None
        and lot["last_bid"] > lot.get("target_price", 0)
    )
    no_bids = total_active - under_budget - over_budget

    # Lots closing within 24 hours
    closing_soon = []
    for lot in active_lots:
        sale_date = lot.get("sale_date")
        if not sale_date:
            continue
        try:
            ts = int(sale_date)
            if ts > 1_000_000_000_000:
                ts /= 1000
            close_time = datetime.fromtimestamp(ts, tz=timezone.utc)
            hours_left = (close_time - now).total_seconds() / 3600
            if 0 < hours_left <= 24:
                closing_soon.append((lot, hours_left))
        except Exception:
            pass

    closing_soon.sort(key=lambda x: x[1])

    date_str = now.strftime("%b %d, %Y")

    lines = [
        f"📊 *Daily Digest — {_esc(date_str)}*",
        "",
        f"🗂 Active lots on watchlist: *{total_active}*",
        f"  ✅ Under budget: {under_budget}",
        f"  ⚠️ Over budget: {over_budget}",
        f"  ➖ No bids yet: {no_bids}",
        f"  🔴 Closed \\(archive\\): {total_closed}",
    ]

    if closing_soon:
        lines += ["", "⏰ *Closing within 24 hours:*"]
        for lot, hours_left in closing_soon[:10]:
            title = _esc(lot.get("title", "Unknown"))
            last_bid = lot.get("last_bid")
            target = lot.get("target_price", 0)
            bid_str = f"\\${last_bid:,.0f}" if last_bid is not None else "no bid"
            target_str = f"\\${target:,}"
            hours_str = f"{hours_left:.1f}h"
            url = lot.get("url", "")
            lines.append(
                f"  • [{title}]({url}) — {bid_str} vs {target_str} target \\| closes in {_esc(hours_str)}"
            )
    else:
        lines += ["", "✅ No lots closing within 24 hours\\."]

    text = "\n".join(lines)

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"},
            )
        if resp.status_code != 200:
            logger.warning("Daily digest failed: %d %s", resp.status_code, resp.text[:200])
        else:
            logger.info("Daily digest sent (%d active, %d closing soon)", total_active, len(closing_soon))
    except Exception as exc:
        logger.warning("Daily digest exception: %s", exc)


def send_bid_alert(token: str, chat_id: str, lot: dict, alert_type: str,
                   current_bid: float = 0, minutes_left: float = None,
                   bid_status: str = None, prev_bid: float = None):
    emoji = ALERT_EMOJIS.get(alert_type, "📢")
    title = lot.get("title", "Unknown")
    target = lot.get("target_price", 0)
    url = lot.get("url", "")
    image_url = lot.get("image_url", "")
    odo = lot.get("odometer", "?")
    damage = lot.get("damage", "?")
    is_nlr = lot.get("is_nlr", False)
    nlr_tag = "✅ NLR" if is_nlr else "🔑 Broker"
    drive = (lot.get("drive_status") or "").title()

    if alert_type == "closing_soon":
        status = f"CLOSING IN {int(minutes_left)} MINS"
    elif alert_type == "sold":
        status = "AUCTION CLOSED"
    elif alert_type == "over_budget":
        status = "BID EXCEEDED YOUR TARGET"
    elif alert_type == "gone":
        status = "LOT MAY HAVE BEEN REMOVED"
    else:
        status = "BID UPDATE"

    # Bid position badge
    if bid_status == "HIGH_BIDDER":
        position_badge = "🟢 YOU'RE WINNING"
    elif bid_status == "OUTBID":
        position_badge = "🔴 YOU'VE BEEN OUTBID"
    else:
        position_badge = ""

    under_by = target - current_bid
    budget_str = (
        f"✅ \\${under_by:,.0f} under your target" if under_by >= 0
        else f"❌ \\${-under_by:,.0f} over budget"
    )
    time_str = f"{int(minutes_left)} min left" if minutes_left is not None else "check Copart for time"
    odo_str = f"{int(odo):,} mi" if isinstance(odo, (int, float)) else str(odo)

    # Show bid movement if we have previous bid
    if prev_bid is not None and prev_bid != current_bid:
        bid_line = f"💰 Bid: *\\${current_bid:,.0f}* \\(was \\${prev_bid:,.0f}\\)"
    else:
        bid_line = f"💰 Current Bid: *\\${current_bid:,.0f}*"

    lines = [
        f"{emoji} *{_esc(status)}* \\| {_esc(nlr_tag)}",
        f"🚗 {_esc(title)}",
    ]

    # Gone alert has a simplified body
    if alert_type == "gone":
        lines += [
            "",
            "This lot has not responded for 3 consecutive checks\\.",
            "It may have been pulled from auction or the lot number changed\\.",
            f"[Check on Copart]({url})",
        ]
    else:
        if position_badge:
            lines.append(f"*{_esc(position_badge)}*")
        lines += [
            bid_line,
            f"🎯 Your Target: \\${target:,}",
            budget_str,
            f"⏱ {_esc(time_str)}",
            f"🔧 {_esc(damage)} \\| {_esc(odo_str)}",
        ]
        if drive:
            lines.append(f"🚦 {_esc(drive)}")
        lines.append(f"[View Lot]({url})")

    text = "\n".join(lines)

    with httpx.Client(timeout=15) as client:
        try:
            if image_url and alert_type != "gone":
                client.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    json={"chat_id": chat_id, "photo": image_url, "caption": text, "parse_mode": "MarkdownV2"},
                )
            else:
                client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"},
                )
        except Exception as e:
            logger.warning("Bid alert send failed: %s", e)
