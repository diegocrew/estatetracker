"""Drop-reason categorization used for the run's INFO-level summary."""

from __future__ import annotations

import pytest

from crawler import projects as projects_mod
from crawler.main import _categorize_drop_reason, collect_project_updates
from crawler.portals.base import PortalError
from crawler.projects import Project
from crawler.state import empty_state


def test_categorizes_known_reasons() -> None:
    cases = {
        "house or land, not a flat": "not a flat (house/land)",
        "non-residential space, not a flat": "not a flat (commercial)",
        "not confirmed to be in search.city 'Bratislava'": "not confirmed in city",
        "locality 'Trencin' does not mention search.city 'Bratislava'": "locality mismatch",
        "banned district 'Vrakuna'": "banned district",
        "district 'Bratislava V' not in search.districts": "district not searched",
        "area 40 m2 below min_area_m2 85": "area too small",
        "area 320 m2 above max_area_m2 300": "area too large",
        "price 1000 EUR below min_price_eur 20000": "price too low",
        "price 1200000 EUR above max_price_eur 1000000": "price too high",
        "price/m2 5000 EUR above max_price_per_m2 4500": "price/m2 too high",
        "rooms 2 below min_rooms 4": "too few rooms",
        "floor 10 above max_floor 5": "floor too high",
        "ground floor excluded": "ground floor excluded",
        "balcony required but listing has none": "no balcony",
        "condition 'povodny_stav' not in allowed_conditions": "condition not allowed",
        "banned street 'Priklad ulica'": "banned street",
        "banned keyword 'drazba'": "banned keyword",
    }
    for reason, expected in cases.items():
        assert _categorize_drop_reason(reason) == expected, reason


def test_unknown_reason_falls_back_to_other() -> None:
    assert _categorize_drop_reason("some future rule message") == "other"


PROJECT_RULES = {"projects": {"enabled": True, "statuses": ["intention", "construction"]}}


def _project(slug: str, status: str = "intention") -> Project:
    return Project(slug=slug, name=slug.title(), url=f"https://www.yimba.sk/{slug}", status=status)


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch):
    """Replace the live YIM.BA fetch with a canned list (or a raised error)."""

    def install(projects: list[Project] | Exception) -> None:
        def fetch(self, rules):
            if isinstance(projects, Exception):
                raise projects
            return projects

        monkeypatch.setattr(projects_mod.YimbaProjects, "fetch", fetch)

    return install


class TestProjectWatch:
    def test_disabled_makes_no_request(self, fake_fetch) -> None:
        fake_fetch(RuntimeError("must not be called"))
        assert collect_project_updates({}, empty_state()) == []

    def test_first_run_seeds_silently(self, fake_fetch) -> None:
        fake_fetch([_project("nove-lido"), _project("zwirn")])
        state = empty_state()
        assert collect_project_updates(PROJECT_RULES, state) == []
        assert set(state["projects"]) == {"nove-lido", "zwirn"}

    def test_second_run_reports_only_the_newcomer(self, fake_fetch) -> None:
        state = empty_state()
        fake_fetch([_project("nove-lido")])
        collect_project_updates(PROJECT_RULES, state)  # seed

        fake_fetch([_project("nove-lido"), _project("zwirn")])
        updates = collect_project_updates(PROJECT_RULES, state)
        assert [u.project.slug for u in updates] == ["zwirn"]
        assert updates[0].is_new

    def test_status_change_is_reported_with_previous_status(self, fake_fetch) -> None:
        state = empty_state()
        fake_fetch([_project("zwirn", "intention")])
        collect_project_updates(PROJECT_RULES, state)  # seed

        fake_fetch([_project("zwirn", "construction")])
        updates = collect_project_updates(PROJECT_RULES, state)
        assert len(updates) == 1
        assert updates[0].previous_status == "intention"
        assert not updates[0].is_new

    def test_unwatched_status_is_ignored(self, fake_fetch) -> None:
        state = empty_state()
        fake_fetch([_project("zwirn", "intention")])
        collect_project_updates(PROJECT_RULES, state)  # seed

        fake_fetch([_project("zwirn", "cancelled")])
        assert collect_project_updates(PROJECT_RULES, state) == []
        assert state["projects"]["zwirn"]["status"] == "cancelled"  # still tracked

    def test_fetch_failure_never_breaks_the_run(self, fake_fetch) -> None:
        fake_fetch(PortalError("blocked"))
        assert collect_project_updates(PROJECT_RULES, empty_state()) == []
