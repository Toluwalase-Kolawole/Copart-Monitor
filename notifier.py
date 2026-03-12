"""
Telegram notifier for Copart Monitor.
NLR lots get a ✅ badge. Broker lots get a 🔑 badge.
Drive status (RUNS AND DRIVES / STATIONARY etc.) and keys shown on every alert.
"""
import logging
import httpx

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
        from datetime import datetime, timezone
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
}


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
            if image_url:
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
