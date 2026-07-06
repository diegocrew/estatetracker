"""Telegram message formatting and the no-network guard."""

from __future__ import annotations

from crawler.models import Condition, Listing
from crawler.notify import MAX_LISTINGS_IN_MESSAGE, TelegramNotifier, telegram_message
from crawler.report import ReportItem


def make_item(**overrides: object) -> ReportItem:
    listing_kwargs: dict = {
        "id": "reality:1", "portal": "reality", "url": "https://example.sk/1",
        "title": "4 izbovy byt", "price_eur": 299000, "area_m2": 92.0, "rooms": "4+",
        "street": None, "district": "Bratislava III", "condition": Condition.NOVOSTAVBA,
    }
    listing_kwargs.update(overrides.pop("listing", {}))  # type: ignore[arg-type]
    item = ReportItem(listing=Listing(**listing_kwargs), score=94, breakdown=[], labels=[])
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def test_message_header_and_block() -> None:
    msg = telegram_message([make_item()], "2026-07-06")
    assert msg.startswith("1 new Bratislava flat(s) - 2026-07-06")
    assert "[94] 299 000 € | 92 m2 | 4+ rooms | Bratislava III | reality" in msg
    assert "https://example.sk/1" in msg


def test_message_price_change() -> None:
    msg = telegram_message([make_item(price_change=(320000, 299000))], "2026-07-06")
    assert "299 000 € (was 320 000 €)" in msg


def test_message_truncates_overflow() -> None:
    items = [make_item(listing={"id": f"r:{i}", "url": f"https://x/{i}"}) for i in range(25)]
    msg = telegram_message(items, "2026-07-06")
    assert msg.count("https://x/") == MAX_LISTINGS_IN_MESSAGE
    assert f"...and {25 - MAX_LISTINGS_IN_MESSAGE} more" in msg


def test_message_is_ascii_safe_symbols_only() -> None:
    # no emoji / em dash leaked into the notification text
    msg = telegram_message([make_item()], "2026-07-06")
    assert "—" not in msg and "🏠" not in msg


def test_disabled_when_unconfigured_does_not_send() -> None:
    notifier = TelegramNotifier(token="", chat_id="")
    assert notifier.enabled is False
    # returns False without any network call
    assert notifier.send("hi") is False
    assert notifier.notify_digest([make_item()], "2026-07-06") is False


def test_notify_digest_empty_is_noop() -> None:
    notifier = TelegramNotifier(token="t", chat_id="c")
    assert notifier.notify_digest([], "2026-07-06") is False
