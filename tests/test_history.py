"""Monthly markdown history archive."""

from __future__ import annotations

import pathlib
from datetime import date

from crawler.history import append_matches, month_path
from crawler.models import Condition, Listing
from crawler.report import ReportItem


def make_item(**overrides: object) -> ReportItem:
    listing_kwargs: dict = {
        "id": "reality:1", "portal": "reality", "url": "https://example.sk/1",
        "title": "4 izbový byt", "price_eur": 299000, "area_m2": 92.0, "rooms": "4+",
        "street": None, "district": "Bratislava III", "condition": Condition.NOVOSTAVBA,
    }
    listing_kwargs.update(overrides.pop("listing", {}))  # type: ignore[arg-type]
    item = ReportItem(listing=Listing(**listing_kwargs), score=94, breakdown=[], labels=[])
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def test_no_items_writes_nothing(tmp_path: pathlib.Path) -> None:
    assert append_matches([], str(tmp_path), today=date(2026, 7, 5)) is None
    assert list(tmp_path.iterdir()) == []


def test_creates_month_file_with_header_and_row(tmp_path: pathlib.Path) -> None:
    path = append_matches([make_item()], str(tmp_path), today=date(2026, 7, 5))
    assert path == month_path(str(tmp_path), date(2026, 7, 5))
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert "# reality-watch matches - July 2026" in text
    assert "| Logged | Score | Price |" in text
    assert "| 2026-07-05 | 94 | 299 000 € | 92 m² | 4+ | Bratislava III |" in text
    assert "[4 izbový byt](https://example.sk/1)" in text


def test_second_append_same_month_adds_row_no_new_header(tmp_path: pathlib.Path) -> None:
    append_matches([make_item()], str(tmp_path), today=date(2026, 7, 5))
    append_matches(
        [make_item(listing={"id": "reality:2", "url": "https://example.sk/2",
                            "title": "Iný byt", "price_eur": 250000})],
        str(tmp_path), today=date(2026, 7, 20),
    )
    text = pathlib.Path(month_path(str(tmp_path), date(2026, 7, 5))).read_text(encoding="utf-8")
    assert text.count("# reality-watch matches") == 1   # header written once
    assert text.count("| Logged | Score |") == 1        # table header written once
    assert "https://example.sk/1" in text and "https://example.sk/2" in text


def test_new_month_new_file(tmp_path: pathlib.Path) -> None:
    append_matches([make_item()], str(tmp_path), today=date(2026, 7, 31))
    append_matches([make_item()], str(tmp_path), today=date(2026, 8, 1))
    assert (tmp_path / "2026-07.md").exists()
    assert (tmp_path / "2026-08.md").exists()


def test_price_change_shows_previous(tmp_path: pathlib.Path) -> None:
    path = append_matches(
        [make_item(price_change=(320000, 299000))], str(tmp_path), today=date(2026, 7, 5)
    )
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert "299 000 € (was 320 000 €)" in text


def test_pipe_in_title_does_not_break_table(tmp_path: pathlib.Path) -> None:
    path = append_matches(
        [make_item(listing={"title": "Byt | 4 izby | Bratislava"})],
        str(tmp_path), today=date(2026, 7, 5),
    )
    text = pathlib.Path(path).read_text(encoding="utf-8")
    # the raw pipes from the title must not appear inside the link label
    assert "Byt / 4 izby / Bratislava" in text
