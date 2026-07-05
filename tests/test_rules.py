"""Rules loading/validation, hard filters, scoring, and the override merge hook."""

from __future__ import annotations

import base64
import copy
import pathlib
from typing import Any

import pytest
import yaml

from crawler.models import Condition, Listing
from crawler.rules import (
    RulesError,
    deep_merge,
    failing_filter,
    labels_for_score,
    load_rules,
    min_score_for_issue,
    score_listing,
    validate_rules,
)

REPO_ROOT = pathlib.Path(__file__).parent.parent


def make_rules(**overrides: Any) -> dict[str, Any]:
    rules: dict[str, Any] = {
        "search": {"city": "Bratislava", "districts": [], "transaction": "predaj"},
        "filters": {
            "min_area_m2": 55,
            "max_price_eur": 260000,
            "max_price_per_m2": 4500,
            "min_rooms": "2",
            "max_floor": None,
            "exclude_ground_floor": True,
            "require_balcony": False,
            "allowed_conditions": [],
            "banned_streets": ["Príklad ulica"],
            "banned_keywords": ["dražba", "spoluvlastnícky podiel"],
        },
        "scoring": {
            "preferred_streets": [{"name": "Obchodná", "bonus": 30}],
            "preferred_districts": [{"name": "Bratislava I", "bonus": 10}],
            "condition_bonus": {"novostavba": 15, "rekonstrukcia": 10},
            "balcony_bonus": 5,
            "price_per_m2_reference": 4000,
        },
        "output": {
            "min_score_for_issue": 0,
            "labels_by_score": [
                {"min": 40, "label": "hot"},
                {"min": 20, "label": "interesting"},
                {"min": 0, "label": "match"},
            ],
        },
    }
    return deep_merge(rules, overrides)


def make_listing(**overrides: Any) -> Listing:
    defaults: dict[str, Any] = {
        "id": "test:1",
        "portal": "test",
        "url": "https://example.sk/1",
        "title": "3 izbový byt",
        "price_eur": 200000,
        "area_m2": 65.0,
        "rooms": "3",
        "street": "Testovacia",
        "district": "Bratislava I – Staré Mesto",
        "floor": 2,
        "condition": Condition.UNKNOWN,
        "balcony": None,
    }
    defaults.update(overrides)
    return Listing(**defaults)


class TestValidation:
    def test_repo_rules_yaml_is_valid(self) -> None:
        load_rules(str(REPO_ROOT / "rules.yaml"), env={})

    def test_missing_section(self) -> None:
        with pytest.raises(RulesError, match="'filters' is missing"):
            validate_rules({"search": {}})

    def test_bad_number(self) -> None:
        with pytest.raises(RulesError, match=r"filters\.max_price_eur"):
            validate_rules(make_rules(filters={"max_price_eur": "lots"}))

    def test_bad_condition_name(self) -> None:
        with pytest.raises(RulesError, match="allowed_conditions"):
            validate_rules(make_rules(filters={"allowed_conditions": ["ruina"]}))

    def test_bad_labels(self) -> None:
        rules = make_rules()
        rules["output"]["labels_by_score"] = [{"min": 10}]
        with pytest.raises(RulesError, match="labels_by_score"):
            validate_rules(rules)

    def test_not_yaml(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "rules.yaml"
        path.write_text("just a string", encoding="utf-8")
        with pytest.raises(RulesError, match="must be a YAML mapping"):
            load_rules(str(path), env={})

    def test_missing_file(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(RulesError, match="not found"):
            load_rules(str(tmp_path / "nope.yaml"), env={})


class TestOverrideMerge:
    def test_b64_override_deep_merges(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "rules.yaml"
        path.write_text(yaml.safe_dump(make_rules()), encoding="utf-8")
        override = {"filters": {"max_price_eur": 300000}}
        env = {"RULES_OVERRIDE_B64": base64.b64encode(yaml.safe_dump(override).encode()).decode()}
        rules = load_rules(str(path), env=env)
        assert rules["filters"]["max_price_eur"] == 300000
        assert rules["filters"]["min_area_m2"] == 55  # untouched sibling key

    def test_invalid_b64_fails_fast(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "rules.yaml"
        path.write_text(yaml.safe_dump(make_rules()), encoding="utf-8")
        with pytest.raises(RulesError, match="RULES_OVERRIDE_B64"):
            load_rules(str(path), env={"RULES_OVERRIDE_B64": "!!! not base64 !!!"})

    def test_deep_merge_does_not_mutate(self) -> None:
        base = {"a": {"b": 1}}
        snapshot = copy.deepcopy(base)
        deep_merge(base, {"a": {"c": 2}})
        assert base == snapshot


class TestHardFilters:
    def test_passing_listing(self) -> None:
        assert failing_filter(make_listing(), make_rules()) is None

    def test_price_too_high(self) -> None:
        reason = failing_filter(make_listing(price_eur=300000), make_rules())
        assert reason is not None and "max_price_eur" in reason

    def test_area_too_small(self) -> None:
        assert failing_filter(make_listing(area_m2=40.0), make_rules()) is not None

    def test_price_per_m2(self) -> None:
        listing = make_listing(price_eur=250000, area_m2=55.0)  # ~4545 €/m²
        reason = failing_filter(listing, make_rules())
        assert reason is not None and "max_price_per_m2" in reason

    def test_min_rooms(self) -> None:
        assert failing_filter(make_listing(rooms="1.5"), make_rules()) is not None
        assert failing_filter(make_listing(rooms="4+"), make_rules()) is None

    def test_ground_floor_excluded(self) -> None:
        assert failing_filter(make_listing(floor=0), make_rules()) == "ground floor excluded"

    def test_district_not_searched(self) -> None:
        rules = make_rules(search={"districts": ["Bratislava I", "Bratislava II"]})
        assert failing_filter(make_listing(district="Bratislava V – Petržalka"), rules)
        assert failing_filter(make_listing(district="Bratislava I – Staré Mesto"), rules) is None

    def test_banned_street_diacritics_insensitive(self) -> None:
        reason = failing_filter(make_listing(street="Priklad ulica 7"), make_rules())
        assert reason is not None and "banned street" in reason

    def test_banned_keyword_diacritics_insensitive(self) -> None:
        listing = make_listing(title="Byt v drazbe - DRAZBA", description_snippet="")
        # rules say "dražba"; the listing says "drazba" — must still match
        rules = make_rules(filters={"banned_keywords": ["dražba"]})
        assert failing_filter(listing, rules) is not None

    def test_missing_data_never_drops(self) -> None:
        listing = make_listing(
            price_eur=None, area_m2=None, rooms=None, floor=None,
            street=None, district=None, balcony=None,
        )
        rules = make_rules(filters={"require_balcony": True})
        assert failing_filter(listing, rules) is None

    def test_require_balcony_drops_confirmed_no(self) -> None:
        rules = make_rules(filters={"require_balcony": True})
        assert failing_filter(make_listing(balcony=False), rules) is not None

    def test_allowed_conditions(self) -> None:
        rules = make_rules(filters={"allowed_conditions": ["novostavba"]})
        assert failing_filter(make_listing(condition=Condition.POVODNY_STAV), rules)
        assert failing_filter(make_listing(condition=Condition.NOVOSTAVBA), rules) is None
        # unknown condition is missing data -> passes
        assert failing_filter(make_listing(condition=Condition.UNKNOWN), rules) is None


class TestScoring:
    def test_score_breakdown(self) -> None:
        listing = make_listing(
            street="Obchodná 12",
            district="Bratislava I – Staré Mesto",
            condition=Condition.REKONSTRUKCIA,
            balcony=True,
            price_eur=200000,
            area_m2=65.0,  # ~3077 €/m², 23% below the 4000 reference
        )
        score, breakdown = score_listing(listing, make_rules())
        assert score == 30 + 10 + 10 + 5 + 23
        assert len(breakdown) == 5

    def test_price_reference_capped(self) -> None:
        listing = make_listing(price_eur=100000, area_m2=100.0)  # 1000 €/m², 75% below
        score, _ = score_listing(listing, make_rules(scoring={
            "preferred_districts": [], "condition_bonus": {},
        }))
        assert score == 25  # capped

    def test_above_reference_penalized(self) -> None:
        listing = make_listing(price_eur=260000, area_m2=59.0, district="Bratislava II")
        score, _ = score_listing(listing, make_rules())
        assert score < 0  # ~4407 €/m² is ~10% above the reference

    def test_no_signals_scores_zero(self) -> None:
        listing = make_listing(street=None, district=None, price_eur=None)
        assert score_listing(listing, make_rules()) == (0, [])


class TestLabels:
    def test_highest_threshold_wins(self) -> None:
        rules = make_rules()
        assert labels_for_score(45, rules) == ["hot"]
        assert labels_for_score(25, rules) == ["interesting"]
        assert labels_for_score(0, rules) == ["match"]
        assert labels_for_score(-5, rules) == []

    def test_min_score_default(self) -> None:
        assert min_score_for_issue(make_rules()) == 0
        assert min_score_for_issue({}) == 0
