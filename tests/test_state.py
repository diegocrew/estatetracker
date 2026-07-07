"""Dedupe, price-change detection, pruning, and portal-health tracking."""

from __future__ import annotations

import pathlib
from datetime import date

from crawler.models import Listing
from crawler.state import (
    classify_listing,
    content_fingerprint,
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


def make_repostable(listing_id: str, price: int | None = 370000) -> Listing:
    """A listing with enough signal (price + area + rooms) to fingerprint."""
    return Listing(
        id=listing_id, portal="bazos", url=f"https://reality.bazos.sk/inzerat/{listing_id}",
        title="4 izbovy byt Bratislava Nove Mesto ulica Nova Roznavska",
        price_eur=price, area_m2=95.0, rooms="4+",
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


class TestContentFingerprintFallback:
    """Some bazos ads get auto-reposted every ~30 min under a brand-new numeric
    ID with identical content, which defeats plain ID-based dedupe."""

    def test_fingerprint_requires_area(self) -> None:
        assert content_fingerprint(make_listing()) is None  # no area_m2
        assert content_fingerprint(make_repostable("1")) is not None

    def test_fingerprint_ignores_price(self) -> None:
        """Price must not be part of identity, or a repost with a changed price
        would look like an unrelated new listing instead of a price change."""
        assert content_fingerprint(make_repostable("1", price=370000)) == content_fingerprint(
            make_repostable("2", price=300000)
        )

    def test_repost_with_new_id_is_recognized_as_seen(self) -> None:
        state = empty_state()
        remember_listing(state, make_repostable("bazos:193414689"), today=date(2026, 7, 7))
        repost = make_repostable("bazos:193415124")  # same content, fresh id
        status, previous = classify_listing(state, repost)
        assert status == "seen"
        assert previous is not None and previous["id"] == "bazos:193414689"

    def test_repost_with_new_id_and_changed_price_is_price_change(self) -> None:
        state = empty_state()
        remember_listing(state, make_repostable("bazos:193414689", price=370000))
        repost = make_repostable("bazos:193415124", price=300000)  # -19%, new id
        assert classify_listing(state, repost)[0] == "price_change"

    def test_different_flat_same_price_area_rooms_is_not_falsely_merged(self) -> None:
        """Different title -> different fingerprint, even with identical numbers."""
        state = empty_state()
        remember_listing(state, make_repostable("bazos:1"))
        other = make_repostable("bazos:2")
        other.title = "Celkom iny byt na inej ulici"
        assert classify_listing(state, other)[0] == "new"

    def test_remember_preserves_first_seen_across_reposts(self) -> None:
        state = empty_state()
        remember_listing(state, make_repostable("bazos:193414689"), today=date(2026, 7, 1))
        repost = make_repostable("bazos:193415124")
        remember_listing(state, repost, today=date(2026, 7, 7))
        assert repost.first_seen == "2026-07-01"

    def test_sparse_listing_falls_back_to_id_only_dedupe(self) -> None:
        """No area_m2 -> no fingerprint -> behaves exactly as before this feature."""
        state = empty_state()
        remember_listing(state, make_listing("test:1"))
        assert classify_listing(state, make_listing("test:2"))[0] == "new"


class TestPrune:
    def test_prunes_stale_keeps_fresh(self) -> None:
        state = empty_state()
        remember_listing(state, make_listing("test:old"), today=date(2026, 1, 1))
        remember_listing(state, make_listing("test:fresh"), today=date(2026, 7, 1))
        removed = prune_state(state, today=date(2026, 7, 5))  # cutoff = 2026-03-07
        assert removed == 1
        assert set(state["listings"]) == {"test:fresh"}

    def test_prunes_stale_content_fingerprints_too(self) -> None:
        state = empty_state()
        remember_listing(state, make_repostable("bazos:old"), today=date(2026, 1, 1))
        fresh = make_repostable("bazos:2")
        fresh.title = "Celkom iny byt na inej ulici"  # different fp than "old"
        remember_listing(state, fresh, today=date(2026, 7, 1))
        prune_state(state, today=date(2026, 7, 5))  # cutoff = 2026-03-07
        assert content_fingerprint(make_repostable("bazos:old")) not in state["content_seen"]
        assert content_fingerprint(fresh) in state["content_seen"]


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
