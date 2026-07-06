"""reality.sk parser.

Targets ``article.offer`` cards (title link, ``.offer__price``,
``.offer__address``, ``.offer__params``, ``.offer__description``). City and
transaction are encoded in the URL path; the rest is filtered client-side.

NOTE: written against a saved fixture - refresh the fixture and selectors when
the canary fires.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from ..models import (
    Listing,
    detect_balcony,
    guess_locality,
    make_listing_id,
    parse_area,
    parse_condition,
    parse_floor,
    parse_price,
    parse_rooms,
)
from .base import MAX_PAGES, BasePortal, split_locality

PORTAL_NAME = "reality"
BASE_URL = "https://www.reality.sk"

_ID_RE = re.compile(r"[-/](\d{4,})/?$")


def build_search_url(rules: dict, page: int) -> str:
    city = (rules.get("search") or {}).get("city", "Bratislava").lower()
    url = f"{BASE_URL}/byty/{city}/predaj/"
    if page > 1:
        url += f"?page={page}"
    return url


def _card_text(card: Tag, selector: str) -> str:
    node = card.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def parse_search_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []
    for card in soup.select("article.offer, div.offer"):
        try:
            link = card.select_one("h2 a[href], a.offer__title[href]") or card.select_one(
                "a[href]"
            )
            if link is None:
                continue
            url = str(link["href"])
            if url.startswith("/"):
                url = BASE_URL + url
            title = link.get_text(" ", strip=True)
            raw_id = card.get("data-id")
            if not raw_id:
                id_match = _ID_RE.search(url)
                raw_id = id_match.group(1) if id_match else None
            params = _card_text(card, ".offer__params")
            description = _card_text(card, ".offer__description") or params
            locality = _card_text(card, ".offer__address")
            street, district = split_locality(locality)
            card_text = card.get_text(" ", strip=True)
            haystack = f"{title} {params} {description} {card_text}"
            district = district or guess_locality(haystack)
            listings.append(
                Listing(
                    id=make_listing_id(PORTAL_NAME, str(raw_id) if raw_id else None, url),
                    portal=PORTAL_NAME,
                    url=url,
                    title=title,
                    price_eur=parse_price(_card_text(card, ".offer__price"))
                    or (parse_price(card_text) if "€" in card_text else None),
                    area_m2=parse_area(params) or parse_area(title) or parse_area(card_text),
                    rooms=parse_rooms(haystack),
                    street=street,
                    district=district,
                    floor=parse_floor(haystack),
                    condition=parse_condition(haystack),
                    balcony=detect_balcony(haystack),
                    description_snippet=description,
                    raw_extra={"locality": locality, "params": params,
                               "card_text": card_text[:2000]},
                )
            )
        except Exception:
            continue
    return listings


class RealitySkPortal(BasePortal):
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
