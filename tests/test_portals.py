"""Parse saved fixtures and assert the normalized output.

EXPECTED TO UPDATE: the fixtures in tests/fixtures/ are synthetic (the portals
were unreachable from the development environment — see tests/fixtures/README.md).
They encode each portal's documented card markup. When the first real crawl
shows selector drift, save real search pages over the fixtures and adjust both
the parsers and these assertions.
"""

from __future__ import annotations

import pathlib

import pytest

from crawler.models import Condition
from crawler.portals import bazos, nehnutelnosti, reality, topreality
from crawler.portals.base import split_locality

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_split_locality() -> None:
    assert split_locality("Obchodná, Bratislava I – Staré Mesto") == (
        "Obchodná",
        "Bratislava I – Staré Mesto",
    )
    assert split_locality("Bratislava IV – Karlova Ves") == (None, "Bratislava IV – Karlova Ves")
    assert split_locality(None) == (None, None)


class TestNehnutelnosti:
    def test_parses_all_cards(self) -> None:
        listings = nehnutelnosti.parse_search_page(load_fixture("nehnutelnosti_search.html"))
        assert len(listings) == 3

    def test_full_card(self) -> None:
        listing = nehnutelnosti.parse_search_page(load_fixture("nehnutelnosti_search.html"))[0]
        assert listing.id == "nehnutelnosti:4901234"
        assert listing.url.startswith("https://www.nehnutelnosti.sk/detail/")
        assert listing.price_eur == 185000
        assert listing.area_m2 == 68.5
        assert listing.rooms == "3"
        assert listing.street == "Obchodná"
        assert listing.district == "Bratislava I – Staré Mesto"
        assert listing.floor == 2
        assert listing.condition is Condition.REKONSTRUKCIA
        assert listing.balcony is True
        assert listing.price_per_m2 == pytest.approx(2700.7, abs=0.1)

    def test_defensive_card(self) -> None:
        """No data-id, 'Cena dohodou', ground floor, novostavba, 'bez balkóna'."""
        listing = nehnutelnosti.parse_search_page(load_fixture("nehnutelnosti_search.html"))[1]
        assert listing.id.startswith("nehnutelnosti:sha1:")  # URL-hash fallback
        assert listing.price_eur is None
        assert listing.price_per_m2 is None
        assert listing.floor == 0
        assert listing.condition is Condition.NOVOSTAVBA
        assert listing.balcony is False

    def test_garsonka(self) -> None:
        listing = nehnutelnosti.parse_search_page(load_fixture("nehnutelnosti_search.html"))[2]
        assert listing.rooms == "1"
        assert listing.price_eur == 99000  # '99.000,- EUR'
        assert listing.street is None
        assert listing.district == "Bratislava IV – Karlova Ves"

    def test_search_url(self) -> None:
        rules = {"search": {"city": "Bratislava"}}
        assert nehnutelnosti.build_search_url(rules, 1).endswith("/bratislava/byty/predaj/")
        assert "p[page]=2" in nehnutelnosti.build_search_url(rules, 2)


class TestTopReality:
    def test_parses_all_cards(self) -> None:
        listings = topreality.parse_search_page(load_fixture("topreality_search.html"))
        assert len(listings) == 2

    def test_full_card(self) -> None:
        listing = topreality.parse_search_page(load_fixture("topreality_search.html"))[0]
        assert listing.id == "topreality:5678901"  # from the URL
        assert listing.price_eur == 239000
        assert listing.area_m2 == 72
        assert listing.rooms == "3"
        assert listing.street == "Záhradnícka"
        assert listing.district == "Bratislava II – Ružinov"
        assert listing.floor == 4
        assert listing.condition is Condition.POVODNY_STAV
        assert listing.balcony is True

    def test_relative_url_and_fallback_description(self) -> None:
        listing = topreality.parse_search_page(load_fixture("topreality_search.html"))[1]
        assert listing.url.startswith("https://www.topreality.sk/")
        assert listing.condition is Condition.NOVOSTAVBA
        assert listing.description_snippet  # falls back to the params line


class TestRealitySk:
    def test_parses_all_cards(self) -> None:
        listings = reality.parse_search_page(load_fixture("reality_search.html"))
        assert len(listings) == 2

    def test_full_card(self) -> None:
        listing = reality.parse_search_page(load_fixture("reality_search.html"))[0]
        assert listing.id == "reality:998877"  # from data-id
        assert listing.price_eur == 210000
        assert listing.area_m2 == 70
        assert listing.rooms == "3"
        assert listing.district == "Bratislava I – Staré Mesto"
        assert listing.floor == 3
        assert listing.condition is Condition.POVODNY_STAV

    def test_loggia_counts_as_balcony(self) -> None:
        listing = reality.parse_search_page(load_fixture("reality_search.html"))[1]
        assert listing.rooms == "4+"
        assert listing.balcony is True
        assert listing.condition is Condition.REKONSTRUKCIA


class TestBazos:
    def test_parses_all_cards(self) -> None:
        listings = bazos.parse_search_page(load_fixture("bazos_search.html"))
        assert len(listings) == 2

    def test_full_card(self) -> None:
        listing = bazos.parse_search_page(load_fixture("bazos_search.html"))[0]
        assert listing.id == "bazos:187654321"
        assert listing.price_eur == 185000
        assert listing.area_m2 == 68
        assert listing.rooms == "3"
        assert listing.street is None  # bazos has no structured street
        assert listing.floor == 5
        assert listing.condition is Condition.REKONSTRUKCIA
        assert listing.balcony is True

    def test_dohodou_card(self) -> None:
        listing = bazos.parse_search_page(load_fixture("bazos_search.html"))[1]
        assert listing.price_eur is None
        assert listing.floor == 0
        assert listing.balcony is False
        assert listing.rooms == "2"

    def test_search_url_uses_server_side_price_filter(self) -> None:
        rules = {"search": {"city": "Bratislava"}, "filters": {"max_price_eur": 260000}}
        url = bazos.build_search_url(rules, 1)
        assert "cenado=260000" in url
        assert "hledat=Bratislava" in url
        assert bazos.build_search_url(rules, 2).startswith(
            "https://reality.bazos.sk/predam/byt/20/"
        )


class TestNehnutelnostiFallback:
    """The 2024+ redesign uses generated CSS classes; the fallback harvests
    /detail/ links and mines the surrounding card text."""

    MODERN_HTML = """
    <html><body><div class="css-x1y2z3">
      <div class="css-a1b2c3">
        <a href="/detail/3-izbovy-byt-ruzinov/Ab4901234">Priestranný 3 izbový byt</a>
        <p class="css-d4e5f6">Ružinov · 68 m² · 3. poschodie</p>
        <span class="css-g7h8i9">185 000 €</span>
      </div>
      <div class="css-a1b2c3">
        <a href="/detail/3-izbovy-byt-ruzinov/Ab4901234">Priestranný 3 izbový byt</a>
      </div>
      <a href="/detail/">not a listing</a>
    </div></body></html>
    """

    def test_harvests_detail_links(self) -> None:
        listings = nehnutelnosti.parse_search_page(self.MODERN_HTML)
        assert len(listings) == 1  # duplicate link + non-listing link ignored
        listing = listings[0]
        assert listing.id == "nehnutelnosti:4901234"
        assert listing.price_eur == 185000
        assert listing.area_m2 == 68
        assert listing.rooms == "3"
        assert listing.floor == 3
        assert listing.has_usable_data

    def test_classic_markup_still_preferred(self) -> None:
        listings = nehnutelnosti.parse_search_page(load_fixture("nehnutelnosti_search.html"))
        assert all(ls.raw_extra.get("parser") != "detail-link-fallback" for ls in listings)


def test_phantom_listing_detection() -> None:
    from crawler.models import Listing

    phantom = Listing(id="x:1", portal="x", url="u", title="whatever")
    assert not phantom.has_usable_data
    assert Listing(id="x:2", portal="x", url="u", title="t", area_m2=60.0).has_usable_data


def test_parsers_survive_garbage_html() -> None:
    for module in (nehnutelnosti, topreality, reality, bazos):
        assert module.parse_search_page("<html><body><p>upgrade your browser") == []
        assert module.parse_search_page("") == []
