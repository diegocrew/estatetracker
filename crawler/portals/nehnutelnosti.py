"""nehnutelnosti.sk — largest Slovak real estate portal.

Parser targets the classic listing-card markup (``div.advertisement-item`` with
a ``data-id`` attribute). City and transaction are filtered server-side through
the URL path; price/area limits are applied later by the rules engine.

NOTE: written against a saved fixture; the live site A/B-tests new markup, so
expect to refresh ``tests/fixtures/nehnutelnosti_search.html`` and the
selectors below when the canary fires.
"""

from __future__ import annotations

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

PORTAL_NAME = "nehnutelnosti"
BASE_URL = "https://www.nehnutelnosti.sk"


def build_search_url(rules: dict, page: int) -> str:
    city = (rules.get("search") or {}).get("city", "Bratislava").lower()
    url = f"{BASE_URL}/{city}/byty/predaj/"
    if page > 1:
        url += f"?p[page]={page}"
    return url


def _card_text(card: Tag, selector: str) -> str:
    node = card.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def parse_search_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []
    for card in soup.select("div.advertisement-item"):
        try:
            link = card.select_one("h2 a[href]") or card.select_one("a[href]")
            if link is None:
                continue
            url = str(link["href"])
            if url.startswith("/"):
                url = BASE_URL + url
            title = link.get_text(" ", strip=True)
            raw_id = card.get("data-id")
            info = _card_text(card, ".advertisement-item--content__info")
            description = _card_text(card, ".advertisement-item--content__description") or info
            locality = _card_text(card, ".advertisement-item--content__locality")
            street, district = split_locality(locality)
            haystack = f"{title} {info} {description}"
            listings.append(
                Listing(
                    id=make_listing_id(PORTAL_NAME, str(raw_id) if raw_id else None, url),
                    portal=PORTAL_NAME,
                    url=url,
                    title=title,
                    price_eur=parse_price(_card_text(card, ".advertisement-item--content__price")),
                    area_m2=parse_area(info) or parse_area(title),
                    rooms=parse_rooms(haystack),
                    street=street,
                    district=district,
                    floor=parse_floor(haystack),
                    condition=parse_condition(haystack),
                    balcony=detect_balcony(haystack),
                    description_snippet=description,
                    raw_extra={"locality": locality, "info": info},
                )
            )
        except Exception:
            continue
    return listings


class NehnutelnostiPortal(BasePortal):
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
