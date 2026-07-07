"""Portal registry - add a new portal by importing it and listing it here."""

from .base import BasePortal, PortalError
from .bazos import BazosPortal
from .byty import BytyPortal
from .nehnutelnosti import NehnutelnostiPortal
from .reality import RealitySkPortal
from .topreality import TopRealityPortal

__all__ = [
    "BasePortal",
    "BazosPortal",
    "BytyPortal",
    "NehnutelnostiPortal",
    "PortalError",
    "RealitySkPortal",
    "TopRealityPortal",
    "all_portals",
]


def all_portals() -> list[BasePortal]:
    """Fresh portal instances in crawl order."""
    return [
        NehnutelnostiPortal(),
        TopRealityPortal(),
        RealitySkPortal(),
        BazosPortal(),
        BytyPortal(),
    ]
