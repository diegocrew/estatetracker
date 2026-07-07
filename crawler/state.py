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
      "content_seen": {
        "<sha1 of portal|title|price|area|rooms>": {
          "first_seen": "2026-07-05", "last_seen": "2026-07-05",
          "id": "<portal:listing_id>", "url": "...", "price_eur": 185000
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

from .models import Listing, normalize_text

DEFAULT_STATE_PATH = os.path.join("state", "seen.json")
PRICE_CHANGE_THRESHOLD = 0.02  # >2% moves re-report the listing
PRUNE_AFTER_DAYS = 120
CANARY_STREAK = 3  # consecutive zero-listing runs before a maintenance Issue

State = dict[str, Any]


def _today() -> date:
    return datetime.now(UTC).date()


def empty_state() -> State:
    return {"listings": {}, "content_seen": {}, "portal_health": {}}


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
    data.setdefault("content_seen", {})
    data.setdefault("portal_health", {})
    return data


def content_fingerprint(listing: Listing) -> str | None:
    """Fallback identity for listings whose portal ID doesn't stay stable.

    Some classifieds ads (observed on bazos) get auto-reposted every ~30
    minutes, and the portal assigns a fresh numeric ID each time - so plain
    ID-based dedupe treats every repost as a brand-new listing forever.
    Fingerprints (portal, title, area, rooms) - deliberately excluding price,
    which is compared separately so a genuine price change on a repost is
    still detected as "price_change" rather than masked as a new fingerprint.
    Returns None when area is missing, so sparse listings fall back to
    ID-only dedupe rather than risk two different flats colliding on title
    alone.
    """
    if listing.area_m2 is None:
        return None
    parts = [
        listing.portal,
        normalize_text(listing.title),
        f"{listing.area_m2:g}",
        listing.rooms or "",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def save_state(state: State, path: str = DEFAULT_STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def _price_changed(old_price: int | None, new_price: int | None) -> bool:
    return bool(
        old_price
        and new_price
        and old_price > 0
        and abs(new_price - old_price) / old_price > PRICE_CHANGE_THRESHOLD
    )


def classify_listing(state: State, listing: Listing) -> tuple[str, dict[str, Any] | None]:
    """('new'|'price_change'|'seen', previous entry if any).

    A listing is worth re-reporting when its price moved by more than
    PRICE_CHANGE_THRESHOLD in either direction. When the portal ID is unseen,
    falls back to the content fingerprint (see content_fingerprint()) before
    concluding a listing is genuinely new.
    """
    entry = state["listings"].get(listing.id)
    if entry is not None:
        if _price_changed(entry.get("price_eur"), listing.price_eur):
            return "price_change", entry
        return "seen", entry

    fp = content_fingerprint(listing)
    if fp:
        content_entry = state["content_seen"].get(fp)
        if content_entry is not None:
            status = "price_change" if _price_changed(
                content_entry.get("price_eur"), listing.price_eur
            ) else "seen"
            return status, content_entry

    return "new", None


def remember_listing(state: State, listing: Listing, today: date | None = None) -> None:
    """Insert or refresh the state entry and stamp listing.first_seen."""
    today_iso = (today or _today()).isoformat()
    entry = state["listings"].get(listing.id)
    fp = content_fingerprint(listing)
    content_entry = state["content_seen"].get(fp) if fp else None
    first_seen = (
        (entry.get("first_seen") if entry else None)
        or (content_entry.get("first_seen") if content_entry else None)
        or today_iso
    )
    listing.first_seen = first_seen
    state["listings"][listing.id] = {
        "first_seen": first_seen,
        "last_seen": today_iso,
        "url": listing.url,
        "hash": hashlib.sha1(listing.url.encode("utf-8")).hexdigest(),
        "price_eur": listing.price_eur,
    }
    if fp:
        state["content_seen"][fp] = {
            "first_seen": first_seen,
            "last_seen": today_iso,
            "id": listing.id,
            "url": listing.url,
            "price_eur": listing.price_eur,
        }


def _prune_bucket(bucket: dict[str, Any], cutoff: str) -> int:
    stale = [
        key
        for key, entry in bucket.items()
        if (entry.get("last_seen") or entry.get("first_seen") or "") < cutoff
    ]
    for key in stale:
        del bucket[key]
    return len(stale)


def prune_state(state: State, today: date | None = None) -> int:
    """Drop listings/fingerprints not seen for PRUNE_AFTER_DAYS; return how many
    listing entries were removed (content_seen entries aren't counted, to keep
    the return value's meaning unchanged)."""
    cutoff = ((today or _today()) - timedelta(days=PRUNE_AFTER_DAYS)).isoformat()
    removed = _prune_bucket(state["listings"], cutoff)
    _prune_bucket(state["content_seen"], cutoff)
    return removed


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
