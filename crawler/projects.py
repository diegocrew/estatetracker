"""YIM.BA development watch: upcoming housing projects, not flats for sale.

This is a *second* feed, deliberately kept out of the listing pipeline. A
project has no price, area or rooms, so it would be discarded by the phantom
guard and every hard filter in ``rules.py``. Instead the watcher diffs the
project list against its own state bucket and reports two events:

* a project appears for the first time (a developer's new intention), and
* a known project changes status ("Zámer" -> "Výstavba", or gets cancelled).

The whole filtered list is rendered server-side on one page, so a run costs a
single request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urljoin

from bs4 import BeautifulSoup, Tag

from .portals.base import PoliteClient

SOURCE_NAME = "yimba"
BASE_URL = "https://www.yimba.sk"
LIST_PATH = "/zoznam-projektov"

# CSS class on the card -> the site's own Slovak label for that status.
STATUS_LABELS: dict[str, str] = {
    "intention": "Zámer",
    "construction": "Výstavba",
    "success": "Zrealizované",
    "cancelled": "Pozastavené / Zrušené",
}
DEFAULT_DISTRICTS = ("downtown", "ruzinov", "stare-mesto")
DEFAULT_TYPE = "Bývanie"

# Thumbnails live under /upload/Projekty/<District>/…, the only place the card
# names the borough.
_DISTRICT_IN_IMAGE_RE = re.compile(r"/upload/Projekty/([^/]+)/", re.IGNORECASE)


@dataclass
class Project:
    """One development project as shown on the YIM.BA project list."""

    slug: str
    name: str
    url: str
    status: str
    district: str | None = None

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)


def build_list_url(rules: dict[str, Any]) -> str:
    """Search URL carrying the type + district filters as YIM.BA expects them."""
    config = rules.get("projects") or {}
    districts = config.get("districts") or list(DEFAULT_DISTRICTS)
    project_type = config.get("type", DEFAULT_TYPE)
    params = [f"type={quote(str(project_type))}"] if project_type else []
    params += [f"district%5B%5D={quote(str(d))}" for d in districts]
    return f"{BASE_URL}{LIST_PATH}?{'&'.join(params)}" if params else f"{BASE_URL}{LIST_PATH}"


def _status_of(card: Tag) -> str:
    classes = [c for c in (card.get("class") or []) if c != "project-list-result"]
    return classes[0] if classes else "unknown"


def _district_of(card: Tag) -> str | None:
    image = card.select_one("img[src]")
    match = _DISTRICT_IN_IMAGE_RE.search(str(image["src"])) if image else None
    if not match:
        return None
    return unquote(match.group(1)).strip() or None


def parse_project_list(html: str) -> list[Project]:
    """Parse the project grid. Malformed cards are skipped, never fatal."""
    soup = BeautifulSoup(html, "lxml")
    projects: list[Project] = []
    seen: set[str] = set()
    for card in soup.select("div.project-list-result"):
        link = card.select_one("h3 a[href]") or card.select_one("a[href]")
        if link is None:
            continue
        href = str(link.get("href") or "")
        slug = href.strip("/").split("/")[-1].split("?")[0]
        if not slug or slug in seen:
            continue
        name = link.get_text(" ", strip=True)
        if not name:
            title = card.select_one("h3")
            name = title.get_text(" ", strip=True) if title else slug
        seen.add(slug)
        projects.append(
            Project(
                slug=slug,
                name=name,
                url=urljoin(BASE_URL, href),
                status=_status_of(card),
                district=_district_of(card),
            )
        )
    return projects


def is_enabled(rules: dict[str, Any]) -> bool:
    return bool((rules.get("projects") or {}).get("enabled"))


def watched_statuses(rules: dict[str, Any]) -> list[str]:
    """Statuses worth reporting; empty list in the rules means all of them."""
    return list((rules.get("projects") or {}).get("statuses") or [])


class YimbaProjects(PoliteClient):
    """Fetches the filtered project list in one polite request."""

    name = SOURCE_NAME
    base_url = BASE_URL

    def fetch(self, rules: dict[str, Any]) -> list[Project]:
        return parse_project_list(self.get(build_list_url(rules)))
