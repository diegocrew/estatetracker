"""Gemini enrichment: fill parsing gaps and add a rich summary per listing.

Deterministic parsing (the portal modules) stays authoritative; Gemini only
fills fields that came back ``None`` / ``Condition.UNKNOWN`` and adds extras
(parking, terrace, year built, red flags, a short summary) into ``raw_extra``.

Auth is a single ``AI_KEY`` secret used as a Gemini API key (Vertex Express
"account-bound" keys work here too). Model and endpoint are overridable via
``AI_MODEL`` / ``AI_API_BASE``. Everything is fail-open: an unset key, a wrong
endpoint, an API error or a bad response returns the listing unchanged and
never breaks a run. Only ``requests`` is used - no GCP SDK.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import requests

from .models import DESCRIPTION_SNIPPET_LEN, Condition, Listing

LOG = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
REQUEST_TIMEOUT_S = 45
MAX_SOURCE_CHARS = 6000  # cap the card text we send to bound tokens
MAX_ERRORS_BEFORE_GIVING_UP = 3  # stop calling after this many failures in one run

# Gemini structured-output schema (OpenAPI subset). `nullable` lets the model
# return null when a field is genuinely absent instead of inventing it.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "price_eur": {"type": "integer", "nullable": True},
        "area_m2": {"type": "number", "nullable": True},
        "rooms": {"type": "string", "nullable": True},
        "street": {"type": "string", "nullable": True},
        "district": {"type": "string", "nullable": True},
        "floor": {"type": "integer", "nullable": True},
        "condition": {
            "type": "string",
            "enum": ["novostavba", "povodny_stav", "rekonstrukcia", "unknown"],
        },
        "balcony": {"type": "boolean", "nullable": True},
        "parking": {"type": "boolean", "nullable": True},
        "terrace": {"type": "boolean", "nullable": True},
        "year_built": {"type": "integer", "nullable": True},
        "is_new_development": {"type": "boolean", "nullable": True},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["summary"],
}

_PROMPT = (
    "You extract structured data from a single Slovak real-estate listing for a "
    "flat in Bratislava. Use ONLY the title and card text below. Return JSON that "
    "matches the provided schema.\n"
    "Rules:\n"
    "- Use null for any field not explicitly present. NEVER guess or invent.\n"
    "- price_eur: the total asking price in EUR as an integer. If the listing says "
    "'cena dohodou', 'cena v RK', 'na vyziadanie' or shows no price, use null. "
    "Never output a price per square metre as the price.\n"
    "- rooms: one of '1', '1.5', '2', '3', '4+'.\n"
    "- district: 'Bratislava - <borough>' (e.g. 'Bratislava - Ruzinov') when "
    "determinable, else the city district as written.\n"
    "- condition: one of novostavba, povodny_stav, rekonstrukcia, unknown.\n"
    "- red_flags: short notes for auction (drazba), co-ownership share "
    "(spoluvlastnicky podiel), sitting tenant, lien, or heavy renovation needed.\n"
    "- summary: ONE or TWO sentences in English capturing the key selling points "
    "(rooms, area, floor, condition, parking/terrace/balcony) and any red flags.\n"
    "Title: {title}\n"
    "Card text: {source}\n"
)


def _source_text(listing: Listing) -> str:
    parts = [listing.title, listing.description_snippet]
    parts += [str(v) for v in listing.raw_extra.values() if v]
    return " ".join(p for p in parts if p)[:MAX_SOURCE_CHARS]


@dataclass
class GeminiEnricher:
    """Per-run enricher; construct once and call ``enrich`` per new listing."""

    api_key: str = field(default_factory=lambda: os.environ.get("AI_KEY", ""))
    model: str = field(default_factory=lambda: os.environ.get("AI_MODEL") or DEFAULT_MODEL)
    api_base: str = field(default_factory=lambda: os.environ.get("AI_API_BASE") or DEFAULT_API_BASE)
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self._error_count = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and self._error_count < MAX_ERRORS_BEFORE_GIVING_UP

    def enrich(self, listing: Listing) -> Listing:
        """Fill gap fields + add extras from Gemini. Returns the listing unchanged
        on any problem (fail-open)."""
        if not self.enabled:
            return listing
        try:
            data = self._generate(_source_text(listing), listing.title)
        except Exception as exc:  # enrichment must never break a run
            self._error_count += 1
            LOG.warning("enrichment failed for %s: %s", listing.id, exc)
            return listing
        if not data:
            return listing
        _apply(listing, data)
        return listing

    def _generate(self, source: str, title: str) -> dict[str, Any] | None:
        url = f"{self.api_base}/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": _PROMPT.format(title=title, source=source)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _RESPONSE_SCHEMA,
                "temperature": 0,
            },
        }
        response = self.session.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)
        if response.status_code != 200:
            self._error_count += 1
            LOG.warning("Gemini HTTP %s: %s", response.status_code, response.text[:300])
            return None
        text = (
            response.json()["candidates"][0]["content"]["parts"][0]["text"]
        )
        return json.loads(text)


def _apply(listing: Listing, data: dict[str, Any]) -> None:
    """Merge model output: deterministic non-None values are never overwritten."""
    price = data.get("price_eur")
    if listing.price_eur is None and isinstance(price, int) and price > 0:
        listing.price_eur = price
    area = data.get("area_m2")
    if listing.area_m2 is None and isinstance(area, int | float) and area > 0:
        listing.area_m2 = float(area)
    if listing.rooms is None and data.get("rooms"):
        listing.rooms = str(data["rooms"])
    if listing.street is None and data.get("street"):
        listing.street = str(data["street"])
    if listing.district is None and data.get("district"):
        listing.district = str(data["district"])
    if listing.floor is None and isinstance(data.get("floor"), int):
        listing.floor = data["floor"]
    if listing.condition is Condition.UNKNOWN and data.get("condition"):
        with contextlib.suppress(ValueError):
            listing.condition = Condition(str(data["condition"]))
    if listing.balcony is None and isinstance(data.get("balcony"), bool):
        listing.balcony = data["balcony"]

    # Extras (new signal) always recorded under raw_extra.
    for key in ("parking", "terrace", "year_built", "is_new_development"):
        if data.get(key) is not None:
            listing.raw_extra[key] = data[key]
    if data.get("red_flags"):
        listing.raw_extra["red_flags"] = list(data["red_flags"])
    summary = (data.get("summary") or "").strip()
    if summary:
        listing.raw_extra["summary"] = summary[:DESCRIPTION_SNIPPET_LEN]
