"""YIM.BA development watch: URL building, parsing, and state diffing."""

from __future__ import annotations

import pathlib
from typing import Any

from crawler.projects import (
    Project,
    build_list_url,
    is_enabled,
    parse_project_list,
    watched_statuses,
)
from crawler.state import classify_project, empty_state, remember_project

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "yimba_projects.html"


def make_rules(**projects: Any) -> dict[str, Any]:
    return {"projects": projects}


def test_build_list_url_carries_type_and_districts() -> None:
    url = build_list_url(make_rules(districts=["downtown", "ruzinov"], type="Bývanie"))
    assert url.startswith("https://www.yimba.sk/zoznam-projektov?")
    assert "type=B%C3%BDvanie" in url
    assert "district%5B%5D=downtown" in url
    assert "district%5B%5D=ruzinov" in url


def test_build_list_url_falls_back_to_defaults() -> None:
    url = build_list_url({})
    assert "district%5B%5D=downtown" in url
    assert "type=B%C3%BDvanie" in url


def test_parses_every_status_from_the_fixture() -> None:
    projects = parse_project_list(FIXTURE.read_text(encoding="utf-8"))
    by_slug = {p.slug: p for p in projects}
    assert len(projects) == 4

    presovska = by_slug["komplex-presovska"]
    assert presovska.name == "Administratívno-obytný komplex Prešovská"
    assert presovska.url == "https://www.yimba.sk/komplex-presovska"
    assert presovska.status == "intention"
    assert presovska.status_label == "Zámer"
    assert presovska.district == "Ružinov"  # mined from the thumbnail path

    assert by_slug["forum-business-center-ii"].status == "construction"
    assert by_slug["bytovy-dom-na-parkovej-ulici"].status == "cancelled"
    assert by_slug["city-house-ruzinov"].status_label == "Zrealizované"


def test_parser_survives_junk_html() -> None:
    assert parse_project_list("<html><body><p>nothing here</p></body></html>") == []


def test_is_enabled_and_watched_statuses() -> None:
    assert is_enabled(make_rules(enabled=True))
    assert not is_enabled({})
    assert watched_statuses(make_rules(statuses=["intention"])) == ["intention"]
    assert watched_statuses({}) == []


def _project(status: str = "intention") -> Project:
    return Project(
        slug="nove-lido", name="Nové Lido", url="https://www.yimba.sk/nove-lido",
        status=status, district="Petržalka",
    )


class TestProjectState:
    def test_first_sighting_is_new(self) -> None:
        state = empty_state()
        assert classify_project(state, _project()) == ("new", None)

    def test_unchanged_project_is_seen(self) -> None:
        state = empty_state()
        remember_project(state, _project())
        assert classify_project(state, _project()) == ("seen", "intention")

    def test_status_transition_is_reported(self) -> None:
        state = empty_state()
        remember_project(state, _project("intention"))
        status, previous = classify_project(state, _project("construction"))
        assert (status, previous) == ("status_change", "intention")

    def test_first_seen_is_kept_across_runs(self) -> None:
        import datetime

        state = empty_state()
        remember_project(state, _project(), today=datetime.date(2026, 1, 1))
        remember_project(state, _project("construction"), today=datetime.date(2026, 6, 1))
        entry = state["projects"]["nove-lido"]
        assert entry["first_seen"] == "2026-01-01"
        assert entry["last_seen"] == "2026-06-01"
        assert entry["status"] == "construction"
