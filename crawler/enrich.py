"""Vertex AI enrichment stub — interface only, no GCP dependency in v1.

Future contract
---------------
``enrich(listing)`` will send ``listing.title`` + ``listing.description_snippet``
to Vertex AI Gemini with a structured-output prompt and fill in fields that
deterministic parsing left as ``None`` / ``Condition.UNKNOWN``:

* ``condition`` (novostavba / povodny_stav / rekonstrukcia)
* ``floor``
* ``balcony``
* orientation and red flags (auction, co-ownership share, lien, tenant in
  place) into ``raw_extra["orientation"]`` / ``raw_extra["red_flags"]``

Deterministically parsed values are authoritative: the model only fills gaps,
never overwrites a non-None field. The call must be fail-open — any API error
returns the listing unchanged. Wire-up lives behind the ``ENRICH_ENABLED``
environment variable (default: disabled) in ``main.py``, so enabling the real
implementation is a drop-in replacement of this function body.
"""

from __future__ import annotations

from .models import Listing


def enrich(listing: Listing) -> Listing:
    """v1 no-op: returns the listing unchanged. See module docstring."""
    return listing
