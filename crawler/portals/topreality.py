"""topreality.sk parser.

Targets the ``div.estate`` card layout (title link in ``h2``, ``span.price``,
``span.locality``, parameters line with rooms/area/floor). City and transaction
are encoded in the URL path; the rest is filtered client-side by the rules
engine.

NOTE: written against a saved fixture — refresh the fixture and selectors when
the canary fires.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag

from ..models import (
    Listing,
    detect_balcony,
    make_listing_id,
    parse_area,
    parse_condition,
    parse_floor,
    parse_price,
    parse_rooms,
)
from .base import MAX_PAGES, BasePortal, split_locality

PORTAL_NAME = "topreality"
BASE_URL = "https://www.topreality.sk"

_ID_RE = re.compile(r"[-/](\d{4,})(?:\.html)?/?$")


def build_search_url(rules: dict, page: int) -> str:
    # /byty/<city>/predaj/ 404s (verified from a live run); the long-standing
    # search endpoint is vyhladavanie-nehnutelnosti-<page>.html with a free-text
    # query. The canary will flag this portal if the endpoint moves again.
    city = (rules.get("search") or {}).get("city", "Bratislava")
    params = {"searchType": "string", "q": city}
    return f"{BASE_URL}/vyhladavanie-nehnutelnosti-{page}.html?{urlencode(params)}"


def _card_text(card: Tag, selector: str) -> str:
    node = card.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def parse_search_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []
    for card in soup.select("div.estate"):
        try:
            link = card.select_one("h2 a[href]") or card.select_one("a[href]")
            if link is None:
                continue
            url = str(link["href"])
            if url.startswith("/"):
                url = BASE_URL + url
            title = link.get_text(" ", strip=True)
            id_match = _ID_RE.search(url)
            raw_id = id_match.group(1) if id_match else None
            params = _card_text(card, ".params") or _card_text(card, ".estate__params")
            description = _card_text(card, ".description") or params
            locality = _card_text(card, ".locality") or _card_text(card, ".estate__locality")
            street, district = split_locality(locality)
            card_text = card.get_text(" ", strip=True)
            haystack = f"{title} {params} {description} {card_text}"
            listings.append(
                Listing(
                    id=make_listing_id(PORTAL_NAME, raw_id, url),
                    portal=PORTAL_NAME,
                    url=url,
                    title=title,
                    price_eur=parse_price(_card_text(card, ".price"))
                    or (parse_price(card_text) if "€" in card_text else None),
                    area_m2=parse_area(params) or parse_area(title) or parse_area(card_text),
                    rooms=parse_rooms(haystack),
                    street=street,
                    district=district,
                    floor=parse_floor(haystack),
                    condition=parse_condition(haystack),
                    balcony=detect_balcony(haystack),
                    description_snippet=description,
                    raw_extra={"locality": locality, "params": params},
                )
            )
        except Exception:
            continue
    return listings


class TopRealityPortal(BasePortal):
    name = PORTAL_NAME
    base_url = BASE_URL

    def fetch(self, rules: dict) -> list[Listing]:
        listings: list[Listing] = []
        seen_ids: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            page_listings = parse_search_page(self.get(build_search_url(rules, page)))
            new = [ls for ls in page_listings if ls.id not in seen_ids]
            if not new:
                break
            seen_ids.update(ls.id for ls in new)
            listings.extend(new)
        return listings
