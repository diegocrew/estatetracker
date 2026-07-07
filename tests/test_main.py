"""Drop-reason categorization used for the run's INFO-level summary."""

from __future__ import annotations

from crawler.main import _categorize_drop_reason


def test_categorizes_known_reasons() -> None:
    cases = {
        "house or land, not a flat": "not a flat (house/land)",
        "not confirmed to be in search.city 'Bratislava'": "not confirmed in city",
        "locality 'Trencin' does not mention search.city 'Bratislava'": "locality mismatch",
        "district 'Bratislava V' not in search.districts": "district not searched",
        "area 40 m2 below min_area_m2 85": "area too small",
        "area 320 m2 above max_area_m2 300": "area too large",
        "price 1000 EUR below min_price_eur 20000": "price too low",
        "price 1200000 EUR above max_price_eur 1000000": "price too high",
        "price/m2 5000 EUR above max_price_per_m2 4500": "price/m2 too high",
        "rooms 2 below min_rooms 4": "too few rooms",
        "floor 10 above max_floor 5": "floor too high",
        "ground floor excluded": "ground floor excluded",
        "balcony required but listing has none": "no balcony",
        "condition 'povodny_stav' not in allowed_conditions": "condition not allowed",
        "banned street 'Priklad ulica'": "banned street",
        "banned keyword 'drazba'": "banned keyword",
    }
    for reason, expected in cases.items():
        assert _categorize_drop_reason(reason) == expected, reason


def test_unknown_reason_falls_back_to_other() -> None:
    assert _categorize_drop_reason("some future rule message") == "other"
