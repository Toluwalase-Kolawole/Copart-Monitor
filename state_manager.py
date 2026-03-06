"""
State management — tracks which lot numbers have already been seen
so we only notify about genuinely new listings.
State is stored as state.json at the repo root and committed back to GitHub.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = Path("state.json")


def load_state(path: Path = DEFAULT_STATE_FILE) -> dict:
    """
    Load existing state from disk.
    Returns a dict with keys:
      - seen_lots: set of lot_number strings
      - last_run: ISO timestamp string
      - total_seen: int
    """
    if not path.exists():
        logger.info("No existing state file, starting fresh")
        return {"seen_lots": [], "last_run": None, "total_seen": 0}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(
            "Loaded state: %d seen lots, last run: %s",
            len(data.get("seen_lots", [])),
            data.get("last_run"),
        )
        return data
    except Exception as e:
        logger.error("Failed to load state file: %s — starting fresh", e)
        return {"seen_lots": [], "last_run": None, "total_seen": 0}


def save_state(state: dict, path: Path = DEFAULT_STATE_FILE) -> None:
    """Persist state to disk."""
    # Cap seen_lots to last 5000 to keep file size manageable
    seen = state.get("seen_lots", [])
    if len(seen) > 5000:
        seen = seen[-5000:]
        state["seen_lots"] = seen

    state["last_run"] = datetime.now(timezone.utc).isoformat()

    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    logger.info("Saved state: %d seen lots", len(seen))


def find_new_lots(lots: list[dict], state: dict) -> list[dict]:
    """
    Given a list of fetched lots and the current state,
    return only the lots that haven't been seen before.
    """
    seen_set = set(state.get("seen_lots", []))
    new_lots = [lot for lot in lots if lot["lot_number"] not in seen_set]
    logger.info(
        "Total fetched: %d | Already seen: %d | New: %d",
        len(lots),
        len(lots) - len(new_lots),
        len(new_lots),
    )
    return new_lots


def mark_seen(lots: list[dict], state: dict) -> dict:
    """Add lot numbers to the seen set and update counts."""
    seen_set = set(state.get("seen_lots", []))
    for lot in lots:
        seen_set.add(lot["lot_number"])

    state["seen_lots"] = list(seen_set)
    state["total_seen"] = state.get("total_seen", 0) + len(lots)
    return state
