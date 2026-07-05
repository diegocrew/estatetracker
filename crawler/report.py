"""GitHub Issue creation via the REST API using the built-in GITHUB_TOKEN.

One Issue per new matching listing, labels created idempotently, a hard cap of
MAX_ISSUES_PER_RUN Issues per run (overflow goes into a single summary Issue so
a rules misconfiguration can't flood the repository), and deduplicated
``scraper-broken`` maintenance Issues for the portal canary.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

import requests

from .models import Listing

LOG = logging.getLogger(__name__)

MAX_ISSUES_PER_RUN = 20
REQUEST_TIMEOUT_S = 30

# label -> (color, description); created on first use, never re-created.
LABEL_DEFINITIONS: dict[str, tuple[str, str]] = {
    "hot": ("E11D48", "High-scoring listing"),
    "interesting": ("F59E0B", "Mid-scoring listing"),
    "match": ("22C55E", "Listing passed all hard filters"),
    "price-drop": ("3B82F6", "Previously seen listing whose price moved >2%"),
    "scraper-broken": ("6B7280", "Portal returned 0 listings for 3 consecutive runs"),
    "digest": ("8B5CF6", "One run's new matches, collected into a single issue"),
}
_DEFAULT_LABEL_COLOR = ("BFDBFE", "reality-watch label")


@dataclass
class ReportItem:
    """Everything needed to open one Issue for one matching listing."""

    listing: Listing
    score: int
    breakdown: list[str]
    labels: list[str]
    price_change: tuple[int, int] | None = None  # (old €, new €) when re-reported


def _fmt_price(price: int | None) -> str:
    return f"{price:,} €".replace(",", " ") if price is not None else "? €"


def _fmt(value: object) -> str:
    return "—" if value is None or value == "" else str(value)


def issue_title(item: ReportItem) -> str:
    listing = item.listing
    area = f"{listing.area_m2:g} m²" if listing.area_m2 else "? m²"
    place = listing.street or listing.district or "?"
    return (
        f"🏠 [{item.score}] {_fmt_price(listing.price_eur)} | {area} | {place} | {listing.portal}"
    )


def issue_body(item: ReportItem) -> str:
    listing = item.listing
    rows = [
        ("Portal", listing.portal),
        ("Price", _fmt_price(listing.price_eur)),
        ("Area", f"{listing.area_m2:g} m²" if listing.area_m2 else "—"),
        ("Price / m²", f"{listing.price_per_m2:g} €" if listing.price_per_m2 else "—"),
        ("Rooms", _fmt(listing.rooms)),
        ("Street", _fmt(listing.street)),
        ("District", _fmt(listing.district)),
        ("Floor", _fmt(listing.floor)),
        ("Condition", listing.condition.value),
        ("Balcony", {True: "yes", False: "no", None: "—"}[listing.balcony]),
        ("First seen", _fmt(listing.first_seen)),
    ]
    lines = [f"### [{listing.title}]({listing.url})", ""]
    if item.price_change:
        old, new = item.price_change
        direction = "dropped" if new < old else "rose"
        lines += [f"**Price {direction}: {_fmt_price(old)} → {_fmt_price(new)}**", ""]
    lines += ["| Field | Value |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    lines += ["", f"**Score: {item.score}**"]
    if item.breakdown:
        lines += ["", "Score breakdown:"]
        lines += [f"- {part}" for part in item.breakdown]
    if listing.description_snippet:
        lines += ["", "> " + listing.description_snippet.replace("\n", " ")]
    lines += ["", f"🔗 {listing.url}"]
    return "\n".join(lines)


def overflow_summary_body(items: list[ReportItem]) -> str:
    lines = [
        f"This run matched more than {MAX_ISSUES_PER_RUN} listings, which usually means",
        "the filters in `rules.yaml` are too loose. The overflow is listed below instead",
        "of opening one Issue each.",
        "",
        "| Score | Listing | Price | Area |",
        "| --- | --- | --- | --- |",
    ]
    for item in items:
        listing = item.listing
        area = f"{listing.area_m2:g} m²" if listing.area_m2 else "—"
        lines.append(
            f"| {item.score} | [{listing.title}]({listing.url}) "
            f"| {_fmt_price(listing.price_eur)} | {area} |"
        )
    return "\n".join(lines)


def digest_title(items: list[ReportItem], date_str: str) -> str:
    return f"🏠 {len(items)} new Bratislava flat(s) — {date_str}"


def digest_body(items: list[ReportItem]) -> str:
    """One markdown table for the whole run: price, area, rooms, address per flat."""
    lines = [
        f"**{len(items)} new matching flat(s)** this run, highest score first.",
        "",
        "| Score | Price | Area | Rooms | Address | Condition | Portal | Link |",
        "| ---: | ---: | ---: | :---: | --- | --- | --- | --- |",
    ]
    for item in items:
        listing = item.listing
        area = f"{listing.area_m2:g} m²" if listing.area_m2 else "—"
        address = listing.street or listing.district or "—"
        flags = " 📉" if item.price_change else ""
        lines.append(
            f"| {item.score}{flags} | {_fmt_price(listing.price_eur)} | {area} "
            f"| {_fmt(listing.rooms)} | {address} | {listing.condition.value} "
            f"| {listing.portal} | [open]({listing.url}) |"
        )
    lines += ["", "📉 = price dropped since last seen. Edit `rules.yaml` to tune matches."]
    return "\n".join(lines)


@dataclass
class Reporter:
    """Thin GitHub REST client; all methods are no-ops when unconfigured."""

    token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    repo: str = field(default_factory=lambda: os.environ.get("GITHUB_REPOSITORY", ""))
    api_root: str = field(
        default_factory=lambda: os.environ.get("GITHUB_API_URL", "https://api.github.com")
    )
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self._ensured_labels: set[str] = set()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.repo)

    def _url(self, path: str) -> str:
        return f"{self.api_root}/repos/{self.repo}{path}"

    def ensure_labels(self, labels: list[str]) -> None:
        """Create labels that don't exist yet; already-existing ones are fine."""
        for name in labels:
            if name in self._ensured_labels:
                continue
            color, description = LABEL_DEFINITIONS.get(name, _DEFAULT_LABEL_COLOR)
            response = self.session.post(
                self._url("/labels"),
                json={"name": name, "color": color, "description": description},
                timeout=REQUEST_TIMEOUT_S,
            )
            if response.status_code not in (201, 422):  # 422 = already exists
                LOG.warning("could not create label %r: HTTP %s", name, response.status_code)
                continue
            self._ensured_labels.add(name)

    def create_issue(self, title: str, body: str, labels: list[str]) -> int | None:
        self.ensure_labels(labels)
        response = self.session.post(
            self._url("/issues"),
            json={"title": title, "body": body, "labels": labels},
            timeout=REQUEST_TIMEOUT_S,
        )
        if response.status_code != 201:
            LOG.error(
                "failed to create issue %r: HTTP %s %s",
                title, response.status_code, response.text[:300],
            )
            return None
        number = response.json().get("number")
        LOG.info("created issue #%s: %s", number, title)
        return number

    def has_open_issue(self, label: str, title_contains: str = "") -> bool:
        response = self.session.get(
            self._url("/issues"),
            params={"labels": label, "state": "open", "per_page": 100},
            timeout=REQUEST_TIMEOUT_S,
        )
        if response.status_code != 200:
            LOG.warning("could not list issues for label %r: HTTP %s",
                        label, response.status_code)
            return False
        return any(title_contains in issue.get("title", "") for issue in response.json())

    def report_digest(self, items: list[ReportItem], date_str: str | None = None) -> int:
        """Open a single Issue for the whole run listing every new match. Returns count."""
        if not items:
            LOG.info("no new matches — no digest issue created")
            return 0
        if not self.enabled:
            LOG.warning("reporter disabled (GITHUB_TOKEN/GITHUB_REPOSITORY unset) — "
                        "%d matches not reported", len(items))
            return 0
        date_str = date_str or datetime.now(UTC).date().isoformat()
        title = digest_title(items, date_str)
        return 1 if self.create_issue(title, digest_body(items), ["digest"]) is not None else 0

    def report_matches(self, items: list[ReportItem]) -> int:
        """Open one Issue per item up to the cap, plus one overflow summary. Returns count."""
        if not self.enabled:
            LOG.warning("reporter disabled (GITHUB_TOKEN/GITHUB_REPOSITORY unset) — "
                        "%d matches not reported", len(items))
            return 0
        created = 0
        for item in items[:MAX_ISSUES_PER_RUN]:
            if self.create_issue(issue_title(item), issue_body(item), item.labels) is not None:
                created += 1
        overflow = items[MAX_ISSUES_PER_RUN:]
        if overflow:
            title = f"⚠️ {len(overflow)} additional matches over the {MAX_ISSUES_PER_RUN}/run cap"
            if self.create_issue(title, overflow_summary_body(overflow), ["match"]) is not None:
                created += 1
        return created

    def report_scraper_broken(self, portal: str, streak: int) -> None:
        """Open one deduplicated maintenance Issue for a silent portal."""
        if not self.enabled:
            return
        if self.has_open_issue("scraper-broken", title_contains=portal):
            LOG.info("scraper-broken issue for %s already open — skipping", portal)
            return
        title = f"⚠️ {portal}: 0 listings for {streak} consecutive runs"
        body = (
            f"The `{portal}` scraper has produced **0 listings for {streak} consecutive "
            "runs**. Likely causes: the portal changed its HTML, the search URL format "
            "moved, or the portal is blocking GitHub Actions IPs.\n\n"
            "Check the workflow logs of the latest `crawl` runs, refresh the fixture in "
            f"`tests/fixtures/`, and update `crawler/portals/{portal}.py`."
        )
        self.create_issue(title, body, ["scraper-broken"])
