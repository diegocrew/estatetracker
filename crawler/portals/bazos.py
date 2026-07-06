"""reality.bazos.sk parser - classifieds, deliberately simple stable HTML.

Card = ``div.inzeraty`` with ``h2.nadpis a`` (title/URL, id in the URL),
``div.inzeratycena`` (price), ``div.inzeratylok`` (locality) and ``div.popis``
(description). Bazos supports server-side filtering by search phrase and max
price (``hledat``/``cenado``); pagination is offset-in-path (20 ads per page).
Bazos has no street/district structure, so ``street`` stays None and
``district`` is whatever locality the seller entered.
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
from .base import MAX_PAGES, BasePortal

PORTAL_NAME = "bazos"
BASE_URL = "https://reality.bazos.sk"
ADS_PER_PAGE = 20

_ID_RE = re.compile(r"/inzerat/(\d+)")


def build_search_url(rules: dict, page: int) -> str:
    search = rules.get("search") or {}
    filters = rules.get("filters") or {}
    params: dict[str, str] = {"hledat": search.get("city", "Bratislava")}
    max_price = filters.get("max_price_eur")
    if max_price:
        params["cenado"] = str(int(max_price))
    offset = (page - 1) * ADS_PER_PAGE
    path = "/predam/byt/" if offset == 0 else f"/predam/byt/{offset}/"
    return f"{BASE_URL}{path}?{urlencode(params)}"


def _card_text(card: Tag, selector: str) -> str:
    node = card.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def parse_search_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []
    for card in soup.select("div.inzeraty"):
        try:
            link = card.select_one("h2 a[href]")
            if link is None:
                continue
            url = str(link["href"])
            if url.startswith("/"):
                url = BASE_URL + url
            title = link.get_text(" ", strip=True)
            id_match = _ID_RE.search(url)
            raw_id = id_match.group(1) if id_match else None
            description = _card_text(card, "div.popis")
            locality = _card_text(card, "div.inzeratylok")
            haystack = f"{title} {description}"
            listings.append(
                Listing(
                    id=make_listing_id(PORTAL_NAME, raw_id, url),
                    portal=PORTAL_NAME,
                    url=url,
                    title=title,
                    price_eur=parse_price(_card_text(card, "div.inzeratycena")),
                    area_m2=parse_area(haystack),
                    rooms=parse_rooms(haystack),
                    street=None,
                    district=locality or None,
                    floor=parse_floor(haystack),
                    condition=parse_condition(haystack),
                    balcony=detect_balcony(haystack),
                    description_snippet=description,
                    raw_extra={"locality": locality},
                )
            )
        except Exception:
            continue
    return listings


class BazosPortal(BasePortal):
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
