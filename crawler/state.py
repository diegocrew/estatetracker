"""Persistence of seen listings and portal health in state/seen.json.

The state file is committed back to the repository by the workflow, which is
the only "database" this project has. Structure:

    {
      "listings": {
        "<portal:listing_id>": {
          "first_seen": "2026-07-05", "last_seen": "2026-07-05",
          "url": "...", "hash": "<sha1 of url>", "price_eur": 185000
        }
      },
      "portal_health": {
        "<portal>": {"zero_streak": 2, "last_error": "...", "last_run": "..."}
      }
    }
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .models import Listing

DEFAULT_STATE_PATH = os.path.join("state", "seen.json")
PRICE_CHANGE_THRESHOLD = 0.02  # >2% moves re-report the listing
PRUNE_AFTER_DAYS = 120
CANARY_STREAK = 3  # consecutive zero-listing runs before a maintenance Issue

State = dict[str, Any]


def _today() -> date:
    return datetime.now(UTC).date()


def empty_state() -> State:
    return {"listings": {}, "portal_health": {}}


def load_state(path: str = DEFAULT_STATE_PATH) -> State:
    if not os.path.exists(path):
        return empty_state()
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return empty_state()
    if "listings" not in data:  # legacy flat layout: the whole file is the listing map
        data = {"listings": data, "portal_health": {}}
    data.setdefault("listings", {})
    data.setdefault("portal_health", {})
    return data


def save_state(state: State, path: str = DEFAULT_STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def classify_listing(state: State, listing: Listing) -> tuple[str, dict[str, Any] | None]:
    """('new'|'price_change'|'seen', previous entry if any).

    A listing is worth re-reporting when its price moved by more than
    PRICE_CHANGE_THRESHOLD in either direction.
    """
    entry = state["listings"].get(listing.id)
    if entry is None:
        return "new", None
    old_price = entry.get("price_eur")
    new_price = listing.price_eur
    if (
        old_price
        and new_price
        and old_price > 0
        and abs(new_price - old_price) / old_price > PRICE_CHANGE_THRESHOLD
    ):
        return "price_change", entry
    return "seen", entry


def remember_listing(state: State, listing: Listing, today: date | None = None) -> None:
    """Insert or refresh the state entry and stamp listing.first_seen."""
    today_iso = (today or _today()).isoformat()
    entry = state["listings"].get(listing.id)
    first_seen = entry.get("first_seen", today_iso) if entry else today_iso
    listing.first_seen = first_seen
    state["listings"][listing.id] = {
        "first_seen": first_seen,
        "last_seen": today_iso,
        "url": listing.url,
        "hash": hashlib.sha1(listing.url.encode("utf-8")).hexdigest(),
        "price_eur": listing.price_eur,
    }


def prune_state(state: State, today: date | None = None) -> int:
    """Drop listings not seen for PRUNE_AFTER_DAYS; return how many were removed."""
    cutoff = ((today or _today()) - timedelta(days=PRUNE_AFTER_DAYS)).isoformat()
    listings = state["listings"]
    stale = [
        key
        for key, entry in listings.items()
        if (entry.get("last_seen") or entry.get("first_seen") or "") < cutoff
    ]
    for key in stale:
        del listings[key]
    return len(stale)


def record_portal_run(
    state: State, portal: str, listing_count: int, error: str | None = None
) -> int:
    """Track the zero-listing streak per portal; return the current streak."""
    health = state["portal_health"].setdefault(portal, {"zero_streak": 0})
    if listing_count > 0 and error is None:
        health["zero_streak"] = 0
    else:
        health["zero_streak"] = int(health.get("zero_streak", 0)) + 1
    health["last_error"] = error
    health["last_run"] = datetime.now(UTC).isoformat(timespec="seconds")
    health["last_count"] = listing_count
    return health["zero_streak"]


def portals_needing_canary(state: State) -> list[tuple[str, int]]:
    """Portals whose zero-streak has reached CANARY_STREAK, with their streak."""
    return [
        (portal, health.get("zero_streak", 0))
        for portal, health in sorted(state["portal_health"].items())
        if health.get("zero_streak", 0) >= CANARY_STREAK
    ]
