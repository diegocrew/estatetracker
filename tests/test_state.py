"""Dedupe, price-change detection, pruning, and portal-health tracking."""

from __future__ import annotations

import pathlib
from datetime import date

from crawler.models import Listing
from crawler.state import (
    classify_listing,
    empty_state,
    load_state,
    portals_needing_canary,
    prune_state,
    record_portal_run,
    remember_listing,
    save_state,
)


def make_listing(listing_id: str = "test:1", price: int | None = 200000) -> Listing:
    return Listing(
        id=listing_id, portal="test", url=f"https://example.sk/{listing_id}",
        title="3 izbový byt", price_eur=price,
    )


class TestDedupe:
    def test_unseen_is_new(self) -> None:
        assert classify_listing(empty_state(), make_listing())[0] == "new"

    def test_remembered_is_seen(self) -> None:
        state = empty_state()
        listing = make_listing()
        remember_listing(state, listing, today=date(2026, 7, 5))
        assert classify_listing(state, listing)[0] == "seen"
        assert listing.first_seen == "2026-07-05"

    def test_first_seen_is_stable(self) -> None:
        state = empty_state()
        remember_listing(state, make_listing(), today=date(2026, 7, 1))
        listing = make_listing()
        remember_listing(state, listing, today=date(2026, 7, 5))
        assert listing.first_seen == "2026-07-01"
        assert state["listings"]["test:1"]["last_seen"] == "2026-07-05"

    def test_small_price_move_is_seen(self) -> None:
        state = empty_state()
        remember_listing(state, make_listing(price=200000))
        assert classify_listing(state, make_listing(price=203000))[0] == "seen"  # +1.5%

    def test_big_price_move_is_price_change(self) -> None:
        state = empty_state()
        remember_listing(state, make_listing(price=200000))
        status, previous = classify_listing(state, make_listing(price=180000))  # -10%
        assert status == "price_change"
        assert previous is not None and previous["price_eur"] == 200000

    def test_price_change_needs_both_prices(self) -> None:
        state = empty_state()
        remember_listing(state, make_listing(price=None))
        assert classify_listing(state, make_listing(price=180000))[0] == "seen"


class TestPrune:
    def test_prunes_stale_keeps_fresh(self) -> None:
        state = empty_state()
        remember_listing(state, make_listing("test:old"), today=date(2026, 1, 1))
        remember_listing(state, make_listing("test:fresh"), today=date(2026, 7, 1))
        removed = prune_state(state, today=date(2026, 7, 5))  # cutoff = 2026-03-07
        assert removed == 1
        assert set(state["listings"]) == {"test:fresh"}


class TestPortalHealth:
    def test_streak_counts_and_resets(self) -> None:
        state = empty_state()
        assert record_portal_run(state, "bazos", 0) == 1
        assert record_portal_run(state, "bazos", 0, error="HTTP 503") == 2
        assert record_portal_run(state, "bazos", 12) == 0

    def test_error_with_listings_still_counts_as_failure(self) -> None:
        state = empty_state()
        assert record_portal_run(state, "bazos", 5, error="anti-bot") == 1

    def test_canary_after_three_zero_runs(self) -> None:
        state = empty_state()
        for _ in range(3):
            record_portal_run(state, "bazos", 0)
        record_portal_run(state, "reality", 7)
        assert portals_needing_canary(state) == [("bazos", 3)]


class TestPersistence:
    def test_roundtrip(self, tmp_path: pathlib.Path) -> None:
        path = str(tmp_path / "state" / "seen.json")
        state = empty_state()
        remember_listing(state, make_listing(), today=date(2026, 7, 5))
        record_portal_run(state, "bazos", 3)
        save_state(state, path)
        loaded = load_state(path)
        assert loaded["listings"]["test:1"]["first_seen"] == "2026-07-05"
        assert loaded["portal_health"]["bazos"]["zero_streak"] == 0

    def test_missing_file_is_empty_state(self, tmp_path: pathlib.Path) -> None:
        assert load_state(str(tmp_path / "nope.json")) == empty_state()

    def test_legacy_flat_layout(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "seen.json"
        path.write_text('{"x:1": {"first_seen": "2026-01-01", "url": "u", "hash": "h"}}')
        loaded = load_state(str(path))
        assert "x:1" in loaded["listings"]
        assert loaded["portal_health"] == {}
