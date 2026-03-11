"""Entry point for auction_tracker.yml — avoids multi-line Python in YAML."""
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

sys.path.insert(0, str(Path(__file__).parent))

from monitor import get_config, run_watchlist_check

config = get_config()
config["watchlist_file"] = Path("watchlist.json")
run_watchlist_check(config)
