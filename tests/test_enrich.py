"""Gemini enrichment: merge logic and fail-open behaviour (no live API calls)."""

from __future__ import annotations

from typing import Any

from crawler.enrich import GeminiEnricher
from crawler.models import Condition, Listing


def make_listing(**overrides: Any) -> Listing:
    defaults: dict[str, Any] = {
        "id": "reality:1", "portal": "reality", "url": "https://example.sk/1",
        "title": "4 izbovy byt", "description_snippet": "Pekny byt", "price_eur": None,
        "area_m2": 92.0, "rooms": "4+", "district": None, "condition": Condition.UNKNOWN,
    }
    defaults.update(overrides)
    return Listing(**defaults)


def enricher_returning(data: dict[str, Any] | None) -> GeminiEnricher:
    enr = GeminiEnricher(api_key="test-key")
    enr._generate = lambda source, title: data  # type: ignore[method-assign]
    return enr


def test_disabled_without_key_returns_unchanged() -> None:
    enr = GeminiEnricher(api_key="")
    assert enr.enabled is False
    listing = make_listing()
    assert enr.enrich(listing) is listing
    assert listing.price_eur is None  # untouched


def test_fills_only_gap_fields() -> None:
    listing = make_listing(price_eur=None, district=None, condition=Condition.UNKNOWN)
    enr = enricher_returning({
        "price_eur": 289000, "district": "Bratislava - Ruzinov",
        "floor": 3, "condition": "novostavba", "balcony": True,
        "parking": True, "summary": "Spacious 4-room new-build with garage.",
    })
    out = enr.enrich(listing)
    assert out.price_eur == 289000
    assert out.district == "Bratislava - Ruzinov"
    assert out.floor == 3
    assert out.condition is Condition.NOVOSTAVBA
    assert out.balcony is True
    assert out.raw_extra["parking"] is True
    assert out.raw_extra["summary"] == "Spacious 4-room new-build with garage."


def test_does_not_overwrite_deterministic_values() -> None:
    listing = make_listing(price_eur=250000, rooms="4+", condition=Condition.REKONSTRUKCIA)
    enr = enricher_returning({
        "price_eur": 999999, "rooms": "2", "condition": "novostavba", "summary": "x",
    })
    out = enr.enrich(listing)
    assert out.price_eur == 250000          # deterministic value kept
    assert out.rooms == "4+"
    assert out.condition is Condition.REKONSTRUKCIA


def test_never_invents_price_when_model_returns_null() -> None:
    listing = make_listing(price_eur=None)
    out = enricher_returning({"price_eur": None, "summary": "Price on request."}).enrich(listing)
    assert out.price_eur is None


def test_red_flags_recorded() -> None:
    enr = enricher_returning({"red_flags": ["drazba"], "summary": "Auction."})
    out = enr.enrich(make_listing())
    assert out.raw_extra["red_flags"] == ["drazba"]


def test_generate_error_is_fail_open() -> None:
    enr = GeminiEnricher(api_key="test-key")

    def boom(source: str, title: str) -> dict[str, Any] | None:
        raise RuntimeError("network down")

    enr._generate = boom  # type: ignore[method-assign]
    listing = make_listing()
    assert enr.enrich(listing) is listing        # unchanged, no raise
    assert enr._error_count == 1


def test_gives_up_after_repeated_errors() -> None:
    enr = GeminiEnricher(api_key="test-key")
    enr._error_count = 3
    assert enr.enabled is False                  # stops calling for the rest of the run


class _FakeResponse:
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {"candidates": [{"content": {"parts": [{"text": '{"summary": "ok"}'}]}}]}


def test_generate_payload_has_user_role() -> None:
    """Vertex rejects contents without a role - the payload must set role=user."""
    captured: dict[str, Any] = {}

    class FakeSession:
        def post(self, url: str, json: dict[str, Any], timeout: int) -> _FakeResponse:
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse()

    enr = GeminiEnricher(api_key="k", api_base="https://example/v1", session=FakeSession())
    result = enr._generate("card text", "title")
    assert result == {"summary": "ok"}
    assert captured["json"]["contents"][0]["role"] == "user"
    assert captured["url"] == "https://example/v1/models/gemini-2.5-flash:generateContent?key=k"
