"""Human-readable monthly match log under history/YYYY-MM.md.

Each run appends that run's new matches to the current month's markdown file, so
the archive stays browsable and no single file grows without bound. The
authoritative dedupe memory is ``state/seen.json``; these files are a derived,
append-only view meant for humans (the crawler never reads them back).
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

from .report import ReportItem

DEFAULT_HISTORY_DIR = "history"

_TABLE_HEADER = (
    "| Logged | Score | Price | Area | Rooms | Address | Condition | Portal | Listing |\n"
    "| --- | ---: | ---: | ---: | :---: | --- | --- | --- | --- |"
)


def _fmt_price(price: int | None) -> str:
    return f"{price:,} €".replace(",", " ") if price is not None else "—"


def _cell(value: str) -> str:
    """Neutralize pipes/newlines so free text can't break the markdown table."""
    return value.replace("|", "/").replace("\n", " ").strip()


def _row(item: ReportItem, logged: str) -> str:
    listing = item.listing
    area = f"{listing.area_m2:g} m²" if listing.area_m2 else "—"
    price = _fmt_price(listing.price_eur)
    if item.price_change:
        old, new = item.price_change
        arrow = "↓" if new < old else "↑"
        price = f"{_fmt_price(new)} ({arrow} {_fmt_price(old)})"
    address = _cell(listing.street or listing.district or "—")
    title = _cell(listing.title) or "listing"
    return (
        f"| {logged} | {item.score} | {price} | {area} | {listing.rooms or '—'} "
        f"| {address} | {listing.condition.value} | {listing.portal} "
        f"| [{title}]({listing.url}) |"
    )


def month_path(history_dir: str, on: date) -> str:
    return os.path.join(history_dir, f"{on:%Y-%m}.md")


def append_matches(
    items: list[ReportItem],
    history_dir: str = DEFAULT_HISTORY_DIR,
    today: date | None = None,
) -> str | None:
    """Append each item as a row in history/<current-month>.md. Returns the path.

    Returns None (and writes nothing) when there are no matches, so an empty run
    never touches the archive.
    """
    if not items:
        return None
    on = today or datetime.now(UTC).date()
    os.makedirs(history_dir, exist_ok=True)
    path = month_path(history_dir, on)
    new_file = not os.path.exists(path)
    rows = [_row(item, on.isoformat()) for item in items]
    with open(path, "a", encoding="utf-8") as fh:
        if new_file:
            fh.write(f"# reality-watch matches — {on:%B %Y}\n\n")
            fh.write(
                "Appended automatically by the crawler each run; newest entries at the "
                "bottom. Do not edit by hand.\n\n"
            )
            fh.write(_TABLE_HEADER + "\n")
        fh.write("\n".join(rows) + "\n")
    return path
