"""
Telegram notification sender for new Copart listings.
Uses the Bot API: https://core.telegram.org/bots/api
"""

import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"


def _escape_md(text: str) -> str:
    """Escape special chars for Telegram MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text


def format_message(lot: dict) -> str:
    """Format a lot dict into a readable Telegram message."""
    lines = ["🚗 *New Copart Listing*\n"]

    title = lot.get("title") or "Unknown"
    lines.append(f"*{_escape_md(title)}*")

    if lot.get("damage"):
        lines.append(f"💥 Damage: {_escape_md(lot['damage'])}")
    if lot.get("odometer"):
        lines.append(f"🔢 Odometer: {_escape_md(str(lot['odometer']))} mi")
    if lot.get("location"):
        lines.append(f"📍 Location: {_escape_md(lot['location'])}")
    if lot.get("sale_date"):
        lines.append(f"📅 Sale Date: {_escape_md(str(lot['sale_date']))}")
    if lot.get("estimate"):
        lines.append(f"💰 Estimate: \\${_escape_md(str(lot['estimate']))}")

    lot_url = lot.get("url", "")
    lot_number = lot.get("lot_number", "")
    if lot_url:
        lines.append(f"\n[🔗 View Lot \\#{_escape_md(lot_number)}]({lot_url})")

    return "\n".join(lines)


def send_telegram(
    token: str,
    chat_id: str,
    lots: list[dict],
    batch_size: int = 20,
) -> bool:
    """
    Send Telegram notifications for a list of new lots.
    Sends a summary first, then individual lot messages.
    Returns True if all messages sent successfully.
    """
    if not lots:
        logger.info("No lots to notify about")
        return True

    success = True

    with httpx.Client(timeout=30) as client:
        # Send summary message first
        summary = (
            f"🔔 *{len(lots)} new Copart listing{'s' if len(lots) > 1 else ''} found\\!*\n"
            f"Sending details below\\.\\.\\."
        )
        _send_text(client, token, chat_id, summary)

        # Send individual lot messages (cap at batch_size to avoid spam)
        for lot in lots[:batch_size]:
            text = format_message(lot)
            image_url = lot.get("image_url")

            if image_url:
                ok = _send_photo(client, token, chat_id, image_url, caption=text)
            else:
                ok = _send_text(client, token, chat_id, text)

            if not ok:
                success = False

        # If more than batch_size, send a final note
        if len(lots) > batch_size:
            overflow = len(lots) - batch_size
            note = f"_\\.\\.\\. and {overflow} more listings\\. Check Copart for full results\\._"
            _send_text(client, token, chat_id, note)

    return success


def _send_text(client: httpx.Client, token: str, chat_id: str, text: str) -> bool:
    url = TELEGRAM_API.format(token=token)
    try:
        resp = client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False,
        })
        data = resp.json()
        if not data.get("ok"):
            logger.error("Telegram sendMessage failed: %s", data)
            return False
        return True
    except Exception as e:
        logger.error("Telegram sendMessage exception: %s", e)
        return False


def _send_photo(
    client: httpx.Client,
    token: str,
    chat_id: str,
    photo_url: str,
    caption: str,
) -> bool:
    url = TELEGRAM_PHOTO_API.format(token=token)
    try:
        resp = client.post(url, json={
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "MarkdownV2",
        })
        data = resp.json()
        if not data.get("ok"):
            # Fall back to text-only if photo fails
            logger.warning("Telegram sendPhoto failed (%s), falling back to text", data.get("description"))
            return _send_text(client, token, chat_id, caption)
        return True
    except Exception as e:
        logger.error("Telegram sendPhoto exception: %s", e)
        return _send_text(client, token, chat_id, caption)


def test_connection(token: str, chat_id: str) -> bool:
    """Send a test message to verify credentials."""
    with httpx.Client(timeout=15) as client:
        return _send_text(
            client, token, chat_id,
            "✅ Copart Monitor connected successfully\\! I\\'ll notify you of new listings here\\."
        )
