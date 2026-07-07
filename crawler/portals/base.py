"""Portal interface plus shared polite-HTTP plumbing.

Politeness contract (applies to every portal):
* single-threaded, max MAX_PAGES result pages per portal per run
* randomized 3–6 s sleep between requests
* realistic browser User-Agent and ``Accept-Language: sk``
* robots.txt is honored when it can be fetched; unreachable robots fails open

Resilience contract: any HTTP error, anti-bot challenge page, or network
failure raises PortalError - the orchestrator records it and moves on to the
next portal; a run never fails because one portal is down.
"""

from __future__ import annotations

import abc
import logging
import random
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests
from bs4 import Tag

from ..models import Listing

LOG = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sk,cs;q=0.8,en;q=0.6",
}
MIN_DELAY_S = 3.0
MAX_DELAY_S = 6.0
MAX_PAGES = 3
REQUEST_TIMEOUT_S = 30

_ANTI_BOT_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf_chl_",
    "attention required",
    "captcha",
    "access denied",
)


class PortalError(RuntimeError):
    """A portal could not be crawled this run (HTTP error, anti-bot, network)."""


def mine_price_text(link: Tag, max_levels: int = 4) -> str:
    """Walk up from a title link to find text that includes its price.

    Used when a card has no dedicated price selector to target: the price is
    somewhere in an ancestor's text. Stops as soon as an ancestor's text
    contains exactly one '€', and deliberately refuses to step into an
    ancestor whose text contains *more than one* - that means the walk has
    crossed the actual card boundary into a wrapper holding several listings,
    which would silently attribute a neighboring card's price to this one.
    """
    container: Tag = link
    text = link.get_text(" ", strip=True)
    for _ in range(max_levels):
        if text.count("€") == 1 or not isinstance(container.parent, Tag):
            break
        parent_text = container.parent.get_text(" ", strip=True)
        if parent_text.count("€") > 1:
            break
        container, text = container.parent, parent_text
    return text


def split_locality(text: str | None) -> tuple[str | None, str | None]:
    """'Obchodná, Bratislava I – Staré Mesto' -> (street, district).

    Portals render locality as comma-separated parts; the part mentioning
    Bratislava (or an 'okres' prefix) is the district, the first other part is
    the street. Missing parts become None.
    """
    if not text:
        return None, None
    street: str | None = None
    district: str | None = None
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        lowered = part.lower()
        if "bratislava" in lowered or lowered.startswith("okres"):
            if district is None:
                district = part.removeprefix("okres").strip() or part
        elif street is None:
            street = part
    return street, district


class BasePortal(abc.ABC):
    """Subclasses implement ``fetch`` and use ``self.get`` for all HTTP."""

    name: str = ""
    base_url: str = ""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._robots: urllib.robotparser.RobotFileParser | None = None
        self._robots_unavailable = False
        self._request_count = 0

    @abc.abstractmethod
    def fetch(self, rules: dict) -> list[Listing]:
        """Crawl up to MAX_PAGES search result pages and return normalized listings."""

    def get(self, url: str) -> str:
        """Politely fetch one page; raise PortalError on anything non-usable."""
        if not self._allowed_by_robots(url):
            raise PortalError(f"{url} is disallowed by robots.txt")
        if self._request_count > 0:
            time.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))
        self._request_count += 1
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as exc:
            raise PortalError(f"request to {url} failed: {exc}") from exc
        if response.status_code in (403, 429, 503):
            raise PortalError(f"HTTP {response.status_code} from {url} (possible anti-bot block)")
        if response.status_code >= 400:
            raise PortalError(f"HTTP {response.status_code} from {url}")
        head = response.text[:4000].lower()
        if any(marker in head for marker in _ANTI_BOT_MARKERS):
            raise PortalError(f"anti-bot challenge page detected at {url}")
        return response.text

    def _allowed_by_robots(self, url: str) -> bool:
        if self._robots_unavailable:
            return True
        if self._robots is None:
            parsed = urlparse(url)
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
            try:
                parser.read()
            except Exception as exc:
                LOG.debug("%s: robots.txt unavailable (%s) - failing open", self.name, exc)
                self._robots_unavailable = True
                return True
            self._robots = parser
        return self._robots.can_fetch(USER_AGENT, url)
