"""Normalized listing model and defensive parsing helpers.

Every helper in this module returns ``None`` (or a sentinel like
``Condition.UNKNOWN``) instead of raising when the input cannot be parsed —
portal HTML drifts constantly and a single malformed card must never kill a run.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

DESCRIPTION_SNIPPET_LEN = 300


class Condition(StrEnum):
    """Condition of the flat, Slovak portal vocabulary."""

    NOVOSTAVBA = "novostavba"
    POVODNY_STAV = "povodny_stav"
    REKONSTRUKCIA = "rekonstrukcia"
    UNKNOWN = "unknown"


def strip_diacritics(text: str) -> str:
    """'Obchodná' -> 'Obchodna'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(text: str | None) -> str:
    """Lowercased, diacritics-stripped, whitespace-collapsed text for comparisons.

    Lets rule authors write either "Obchodna" or "Obchodná".
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", strip_diacritics(text).lower()).strip()


def matches_place(needle: str, haystack: str | None) -> bool:
    """Case/diacritics-insensitive whole-word match for streets and districts.

    'Bratislava I' matches 'Bratislava I \u2013 Star\u00e9 Mesto' but NOT 'Bratislava II'
    (a plain substring check would).
    """
    needle_norm = normalize_text(needle)
    haystack_norm = normalize_text(haystack)
    if not needle_norm or not haystack_norm:
        return False
    return re.search(rf"\b{re.escape(needle_norm)}\b", haystack_norm) is not None


_PRICE_RE = re.compile(r"\d[\d\s.\u00a0\u202f]*(?:,\d+)?")
_PRICE_NEAR_EUR_RE = re.compile(
    r"(\d[\d\s.\u00a0\u202f]*(?:,\d+)?)\s*(?:,-)?\s*(?:€|eur)", re.IGNORECASE
)


def _to_int(number: str) -> int | None:
    number = number.split(",")[0]  # drop decimal part / ',-' suffix
    number = re.sub(r"[\s.\u00a0\u202f]", "", number)
    try:
        value = int(number)
    except ValueError:
        return None
    return value if value > 0 else None


def parse_price(text: str | None) -> int | None:
    """'185 000 €', '185.000,- EUR', '185000' -> 185000. None when unparseable.

    In mixed text ('3 izbový byt, 68 m², 185 000 € (2 701 €/m²)') numbers
    adjacent to a €/EUR sign win, and the largest of them is taken so that the
    total price beats the per-m² figure. 'Cena dohodou' (price on request) is
    treated as no price.
    """
    if not text:
        return None
    if "dohod" in normalize_text(text):
        return None
    near_eur = [
        value
        for match in _PRICE_NEAR_EUR_RE.finditer(text)
        if (value := _to_int(match.group(1))) is not None
    ]
    if near_eur:
        return max(near_eur)
    match = _PRICE_RE.search(text)
    return _to_int(match.group(0)) if match else None


_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m(?:2|²|\b)", re.IGNORECASE)


def parse_area(text: str | None) -> float | None:
    """'62,5 m²' -> 62.5. None when unparseable."""
    if not text:
        return None
    match = _AREA_RE.search(text)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


_ROOMS_RE = re.compile(r"(\d+(?:[.,]5)?)\s*[-\s]?\s*izb")


def parse_rooms(text: str | None) -> str | None:
    """Slovak room convention: '1', '1.5', '2', '3', '4+'.

    '3 izbový byt' -> '3', 'garsónka' -> '1', '5-izbový' -> '4+'.
    """
    norm = normalize_text(text)
    if not norm:
        return None
    if "garson" in norm or "garzon" in norm:
        return "1"
    match = _ROOMS_RE.search(norm)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    if value >= 4:
        return "4+"
    if value == int(value):
        return str(int(value))
    return str(value)


def rooms_to_float(rooms: str | None) -> float | None:
    """'4+' -> 4.0, '1.5' -> 1.5 — for numeric comparison against min_rooms."""
    if rooms is None:
        return None
    try:
        return float(rooms.rstrip("+"))
    except ValueError:
        return None


_FLOOR_RE = re.compile(r"(-?\d+)\s*\.?\s*(?:poschodie|posch\b)")
_NP_RE = re.compile(r"(\d+)\s*\.?\s*np\b")


def parse_floor(text: str | None) -> int | None:
    """'3. poschodie' -> 3, 'prízemie' -> 0, '2. NP' -> 1 (NP is 1-based)."""
    norm = normalize_text(text)
    if not norm:
        return None
    if "prizemie" in norm:
        return 0
    match = _FLOOR_RE.search(norm)
    if match:
        return int(match.group(1))
    match = _NP_RE.search(norm)
    if match:
        return int(match.group(1)) - 1
    return None


def parse_condition(text: str | None) -> Condition:
    norm = normalize_text(text)
    if not norm:
        return Condition.UNKNOWN
    if "novostavb" in norm:
        return Condition.NOVOSTAVBA
    if "rekonstru" in norm:  # matches 'rekonštrukcia', 'po rekonštrukcii', ...
        return Condition.REKONSTRUKCIA
    if "povodn" in norm:
        return Condition.POVODNY_STAV
    return Condition.UNKNOWN


def detect_balcony(text: str | None) -> bool | None:
    """True/False when the text says so, None when it doesn't mention it."""
    norm = normalize_text(text)
    if not norm:
        return None
    if re.search(r"bez\s+balkon", norm):
        return False
    if "balkon" in norm or "lodzi" in norm or "loggi" in norm:
        return True
    return None


def make_listing_id(portal: str, raw_id: str | None, url: str) -> str:
    """Portal-prefixed stable ID; SHA-1 of the URL when the portal exposes no ID."""
    if raw_id:
        return f"{portal}:{raw_id}"
    return f"{portal}:sha1:{hashlib.sha1(url.encode('utf-8')).hexdigest()}"


@dataclass
class Listing:
    """Normalized listing — the single schema every portal parser produces."""

    id: str
    portal: str
    url: str
    title: str
    price_eur: int | None = None
    area_m2: float | None = None
    rooms: str | None = None
    street: str | None = None
    district: str | None = None
    floor: int | None = None
    condition: Condition = Condition.UNKNOWN
    balcony: bool | None = None
    description_snippet: str = ""
    first_seen: str = ""
    raw_extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.description_snippet = (self.description_snippet or "")[:DESCRIPTION_SNIPPET_LEN]

    @property
    def has_usable_data(self) -> bool:
        """False = phantom card: parser drift produced a listing with nothing in it.

        Such listings must not reach filtering/reporting and count as a parser
        failure for the portal-health canary.
        """
        return self.price_eur is not None or self.area_m2 is not None or self.rooms is not None

    @property
    def price_per_m2(self) -> float | None:
        if self.price_eur is None or not self.area_m2:
            return None
        return round(self.price_eur / self.area_m2, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "portal": self.portal,
            "url": self.url,
            "title": self.title,
            "price_eur": self.price_eur,
            "area_m2": self.area_m2,
            "price_per_m2": self.price_per_m2,
            "rooms": self.rooms,
            "street": self.street,
            "district": self.district,
            "floor": self.floor,
            "condition": self.condition.value,
            "balcony": self.balcony,
            "description_snippet": self.description_snippet,
            "first_seen": self.first_seen,
            "raw_extra": self.raw_extra,
        }
