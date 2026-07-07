"""byty.sk - Slovak real estate portal.

Speculative parser: unlike the other four portals, this one was built with
zero reference HTML - byty.sk was unreachable from the development
environment (fetch attempts return HTTP 403, same as the other portals; only
GitHub Actions' own runners get through). Rather than guess specific CSS
class names with no basis, this harvests any link whose path contains a 5+
digit numeric ID (the common convention for ad IDs across Slovak real estate
sites) and mines price/area/rooms from the surrounding text - the same
markup-agnostic strategy that already works as nehnutelnosti.py's fallback.
Expect a real-fixture correction pass once the first live run's output
(portal health, drop-reason tally) shows what needs fixing.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

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
from .base import MAX_PAGES, BasePortal, mine_price_text

PORTAL_NAME = "byty"
BASE_URL = "https://www.byty.sk"

_ID_IN_PATH_RE = re.compile(r"/(\d{5,})(?:[/?-]|\.html|$)")
_SKIP_HREF_RE = re.compile(r"^(#|javascript:|mailto:|tel:)")
MIN_TITLE_CHARS = 5  # filters out icon/thumbnail links with no real title text


def build_search_url(rules: dict, page: int) -> str:
    city = (rules.get("search") or {}).get("city", "Bratislava").lower()
    url = f"{BASE_URL}/byty/predaj/{city}"
    if page > 1:
        url += f"?strana={page}"
    return url


def parse_search_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []
    seen_urls: set[str] = set()
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        if _SKIP_HREF_RE.match(href):
            continue
        id_match = _ID_IN_PATH_RE.search(href)
        if not id_match:
            continue
        url = (BASE_URL + href if href.startswith("/") else href).split("?")[0].rstrip("/")
        if not url.startswith(BASE_URL) or url in seen_urls:
            continue
        title = link.get_text(" ", strip=True)
        if len(title) < MIN_TITLE_CHARS:
            continue
        seen_urls.add(url)
        try:
            text = mine_price_text(link)
            haystack = f"{title} {text}"
            listings.append(
                Listing(
                    id=make_listing_id(PORTAL_NAME, id_match.group(1), url),
                    portal=PORTAL_NAME,
                    url=url,
                    title=title,
                    price_eur=parse_price(text) if "€" in text else None,
                    area_m2=parse_area(haystack),
                    rooms=parse_rooms(haystack),
                    district=guess_locality(haystack),
                    floor=parse_floor(haystack),
                    condition=parse_condition(haystack),
                    balcony=detect_balcony(haystack),
                    description_snippet=text[:300],
                    raw_extra={"parser": "detail-link-harvest", "card_text": text[:2000]},
                )
            )
        except Exception:
            continue
    return listings


class BytyPortal(BasePortal):
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
