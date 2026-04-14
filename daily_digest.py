"""
Daily digest entry point — sends a Telegram summary of the current watchlist.
Invoked by the daily_digest.yml GitHub Actions workflow at 8 AM UTC.
"""
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("daily_digest")

sys.path.insert(0, str(Path(__file__).parent))

from monitor import get_config
from notifier import send_daily_digest
from auction_tracker import load_watchlist

config = get_config()

watchlist_path = Path("watchlist.json")
archive_path = Path("watchlist_archive.json")

watchlist = load_watchlist(watchlist_path)

archive: dict = {}
if archive_path.exists():
    try:
        archive = json.loads(archive_path.read_text())
    except Exception as exc:
        logger.warning("Could not read archive: %s", exc)

send_daily_digest(
    token=config["telegram_token"],
    chat_id=config["telegram_chat_id"],
    watchlist=watchlist,
    archive=archive,
)
