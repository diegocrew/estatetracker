"""Issue title/body formatting (pure functions - no network)."""

from __future__ import annotations

from crawler.models import Condition, Listing
from crawler.report import (
    MAX_ISSUES_PER_RUN,
    ReportItem,
    digest_body,
    digest_title,
    issue_body,
    issue_title,
    overflow_summary_body,
)


def make_item(**overrides: object) -> ReportItem:
    listing = Listing(
        id="bazos:1", portal="bazos", url="https://example.sk/1",
        title="Predám 3 izbový byt", price_eur=185000, area_m2=68.0,
        rooms="3", street="Obchodná", district="Bratislava I",
        floor=5, condition=Condition.REKONSTRUKCIA, balcony=True,
        description_snippet="Slnečný byt.", first_seen="2026-07-05",
    )
    item = ReportItem(listing=listing, score=42, breakdown=["+30 preferred street 'Obchodná'"],
                      labels=["hot"])
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def test_issue_title_format() -> None:
    assert issue_title(make_item()) == "[42] 185 000 € | 68 m² | Obchodná | bazos"


def test_issue_title_handles_missing_fields() -> None:
    item = make_item()
    item.listing.price_eur = None
    item.listing.area_m2 = None
    item.listing.street = None
    assert issue_title(item) == "[42] ? € | ? m² | Bratislava I | bazos"


def test_issue_body_contains_table_link_and_breakdown() -> None:
    body = issue_body(make_item())
    assert "| Price | 185 000 € |" in body
    assert "https://example.sk/1" in body
    assert "+30 preferred street" in body
    assert "> Slnečný byt." in body


def test_price_change_banner() -> None:
    body = issue_body(make_item(price_change=(200000, 185000)))
    assert "Price dropped: 200 000 € -> 185 000 €" in body


def test_overflow_summary_lists_items() -> None:
    body = overflow_summary_body([make_item()])
    assert str(MAX_ISSUES_PER_RUN) in body
    assert "[Predám 3 izbový byt](https://example.sk/1)" in body


def test_digest_title() -> None:
    items = [make_item(), make_item()]
    assert digest_title(items, "2026-07-05") == "2 new Bratislava flat(s) - 2026-07-05"


def test_digest_body_one_row_per_flat() -> None:
    body = digest_body([make_item(score=42), make_item(score=10)])
    assert body.count("[open](https://example.sk/1)") == 2
    assert "185 000 €" in body  # price column
    assert "68 m²" in body      # area column
    assert "Obchodná" in body   # address column


def test_digest_body_marks_price_drop() -> None:
    body = digest_body([make_item(price_change=(200000, 185000))])
    assert "185 000 € (was 200 000 €)" in body
