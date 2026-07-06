"""Telegram delivery of the run digest via the Bot API.

Sends one message per run listing that run's new matches, so the digest reaches
your phone without opening the repo. Disabled (a no-op) unless both
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set, mirroring the Reporter: the
crawler never fails a run because notification was not configured or the send
failed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import requests

from .report import ReportItem, _fmt_price

LOG = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 30
TELEGRAM_MAX_CHARS = 4096
MAX_LISTINGS_IN_MESSAGE = 20  # keep well under the 4096-char Telegram limit


def telegram_message(items: list[ReportItem], date_str: str) -> str:
    """Plain-text message: a header plus one block per flat with a raw URL.

    Raw URLs are used (no HTML/Markdown parse mode) so listing titles never need
    escaping and Telegram still auto-links them.
    """
    header = f"{len(items)} new Bratislava flat(s) - {date_str}"
    lines = [header]
    for item in items[:MAX_LISTINGS_IN_MESSAGE]:
        listing = item.listing
        area = f"{listing.area_m2:g} m2" if listing.area_m2 else "? m2"
        rooms = f"{listing.rooms} rooms" if listing.rooms else "? rooms"
        place = listing.street or listing.district or "?"
        price = _fmt_price(listing.price_eur)
        if item.price_change:
            old, new = item.price_change
            price = f"{_fmt_price(new)} (was {_fmt_price(old)})"
        block = f"\n[{item.score}] {price} | {area} | {rooms} | {place} | {listing.portal}"
        summary = listing.raw_extra.get("summary")
        if summary:
            block += f"\n{summary}"
        block += f"\n{listing.url}"
        lines.append(block)
    overflow = len(items) - MAX_LISTINGS_IN_MESSAGE
    if overflow > 0:
        lines.append(f"\n...and {overflow} more (see the GitHub issue).")
    message = "\n".join(lines)
    if len(message) > TELEGRAM_MAX_CHARS:
        message = message[: TELEGRAM_MAX_CHARS - 3] + "..."
    return message


@dataclass
class TelegramNotifier:
    """Thin Telegram Bot API client; a no-op when unconfigured."""

    token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))
    api_root: str = "https://api.telegram.org"
    session: requests.Session = field(default_factory=requests.Session)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        """POST one message; return True on success. Never raises."""
        if not self.enabled:
            LOG.info("Telegram disabled (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID unset) - skipping")
            return False
        url = f"{self.api_root}/bot{self.token}/sendMessage"
        try:
            response = self.session.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            LOG.warning("Telegram send failed: %s", exc)
            return False
        if response.status_code != 200:
            LOG.warning("Telegram send failed: HTTP %s %s",
                        response.status_code, response.text[:300])
            return False
        LOG.info("sent Telegram notification")
        return True

    def notify_digest(self, items: list[ReportItem], date_str: str) -> bool:
        if not items:
            return False
        return self.send(telegram_message(items, date_str))
