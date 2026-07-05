"""Load and validate rules.yaml, apply hard filters, compute scores.

The rules file is user-editable; validation fails fast with a message that names
the exact offending key. An optional ``RULES_OVERRIDE_B64`` environment variable
(base64-encoded YAML) is deep-merged over the file at load time so that
sensitive preferences (streets, budget) can live in a private Actions variable
instead of the public repo.
"""

from __future__ import annotations

import base64
import copy
import os
from collections.abc import Mapping
from typing import Any

import yaml

from .models import Condition, Listing, matches_place, normalize_text, rooms_to_float

DEFAULT_RULES_PATH = "rules.yaml"
OVERRIDE_ENV_VAR = "RULES_OVERRIDE_B64"

# Price-per-m² scoring: ±1 point per % below/above the reference, capped at ±25.
PRICE_REF_CAP = 25

_VALID_CONDITIONS = {c.value for c in Condition}


class RulesError(ValueError):
    """rules.yaml (or its override) is missing, malformed, or fails validation."""


def deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` over ``base`` without mutating either."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_rules(
    path: str = DEFAULT_RULES_PATH, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    env = os.environ if env is None else env
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise RulesError(f"rules file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise RulesError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError(f"{path} must be a YAML mapping, got {type(data).__name__}")

    override_b64 = (env.get(OVERRIDE_ENV_VAR) or "").strip()
    if override_b64:
        try:
            override = yaml.safe_load(base64.b64decode(override_b64, validate=True))
        except Exception as exc:
            raise RulesError(f"{OVERRIDE_ENV_VAR} is not base64-encoded YAML: {exc}") from exc
        if not isinstance(override, dict):
            raise RulesError(f"{OVERRIDE_ENV_VAR} must decode to a YAML mapping")
        data = deep_merge(data, override)

    validate_rules(data)
    return data


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RulesError(f"rules.yaml: {message}")


def _check_number(value: Any, key: str) -> None:
    _require(
        value is None or isinstance(value, int | float),
        f"{key} must be a number or null, got {value!r}",
    )


def _check_str_list(value: Any, key: str) -> None:
    _require(isinstance(value, list), f"{key} must be a list")
    for item in value:
        _require(isinstance(item, str), f"{key} entries must be strings, got {item!r}")


def _check_bonus_list(value: Any, key: str) -> None:
    _require(isinstance(value, list), f"{key} must be a list of {{name, bonus}} entries")
    for item in value:
        _require(isinstance(item, dict), f"{key} entries must be mappings, got {item!r}")
        _require(isinstance(item.get("name"), str), f"{key} entries need a string 'name'")
        _check_number(item.get("bonus", 0), f"{key}[{item.get('name')!r}].bonus")


def validate_rules(rules: dict[str, Any]) -> None:
    """Raise RulesError naming the offending key when the structure is wrong."""
    for section in ("search", "filters"):
        _require(isinstance(rules.get(section), dict), f"required section '{section}' is missing")
    for section in ("scoring", "output"):
        _require(
            rules.get(section) is None or isinstance(rules.get(section), dict),
            f"section '{section}' must be a mapping",
        )

    search = rules["search"]
    _require(isinstance(search.get("city", ""), str), "search.city must be a string")
    _check_str_list(search.get("districts") or [], "search.districts")
    _require(isinstance(search.get("transaction", "predaj"), str),
             "search.transaction must be a string")

    filters = rules["filters"]
    for key in ("min_area_m2", "min_price_eur", "max_price_eur", "max_price_per_m2", "max_floor"):
        _check_number(filters.get(key), f"filters.{key}")
    for key in ("exclude_ground_floor", "require_balcony"):
        value = filters.get(key, False)
        _require(isinstance(value, bool), f"filters.{key} must be true or false, got {value!r}")
    min_rooms = filters.get("min_rooms")
    _require(
        min_rooms is None or isinstance(min_rooms, str | int | float),
        f"filters.min_rooms must be a string like \"2\" or null, got {min_rooms!r}",
    )
    if min_rooms is not None:
        _require(
            rooms_to_float(str(min_rooms)) is not None,
            f"filters.min_rooms is not a valid room count: {min_rooms!r}",
        )
    allowed = filters.get("allowed_conditions") or []
    _check_str_list(allowed, "filters.allowed_conditions")
    for cond in allowed:
        _require(
            cond in _VALID_CONDITIONS,
            f"filters.allowed_conditions: {cond!r} is not one of {sorted(_VALID_CONDITIONS)}",
        )
    _check_str_list(filters.get("banned_streets") or [], "filters.banned_streets")
    _check_str_list(filters.get("banned_keywords") or [], "filters.banned_keywords")

    scoring = rules.get("scoring") or {}
    _check_bonus_list(scoring.get("preferred_streets") or [], "scoring.preferred_streets")
    _check_bonus_list(scoring.get("preferred_districts") or [], "scoring.preferred_districts")
    condition_bonus = scoring.get("condition_bonus") or {}
    _require(isinstance(condition_bonus, dict), "scoring.condition_bonus must be a mapping")
    for cond, bonus in condition_bonus.items():
        _require(
            cond in _VALID_CONDITIONS,
            f"scoring.condition_bonus: {cond!r} is not one of {sorted(_VALID_CONDITIONS)}",
        )
        _check_number(bonus, f"scoring.condition_bonus.{cond}")
    _check_number(scoring.get("balcony_bonus"), "scoring.balcony_bonus")
    _check_number(scoring.get("price_per_m2_reference"), "scoring.price_per_m2_reference")

    output = rules.get("output") or {}
    _check_number(output.get("min_score_for_issue"), "output.min_score_for_issue")
    labels = output.get("labels_by_score") or []
    _require(isinstance(labels, list), "output.labels_by_score must be a list")
    for entry in labels:
        _require(isinstance(entry, dict), "output.labels_by_score entries must be mappings")
        _check_number(entry.get("min"), "output.labels_by_score[].min")
        _require(
            isinstance(entry.get("label"), str),
            "output.labels_by_score entries need a string 'label'",
        )


def failing_filter(listing: Listing, rules: dict[str, Any]) -> str | None:
    """Return the human-readable reason a hard filter drops this listing, or None.

    Missing data (None fields) never causes a drop — only a confirmed violation
    does. The Vertex AI enrichment step will fill those gaps later.
    """
    filters = rules.get("filters") or {}
    search = rules.get("search") or {}
    districts = search.get("districts") or []

    # Classifieds portals carry seller-entered localities, so a search for
    # Bratislava can return e.g. Trenčín; district is only set when the parser
    # found a locality, and that locality must mention the searched city.
    city = search.get("city")
    if city and listing.district and normalize_text(city) not in normalize_text(listing.district):
        return f"locality {listing.district!r} does not mention search.city {city!r}"

    if (
        districts
        and listing.district is not None
        and not any(matches_place(d, listing.district) for d in districts)
    ):
        return f"district {listing.district!r} not in search.districts"

    min_area = filters.get("min_area_m2")
    if min_area is not None and listing.area_m2 is not None and listing.area_m2 < min_area:
        return f"area {listing.area_m2:g} m² below min_area_m2 {min_area}"

    min_price = filters.get("min_price_eur")
    if min_price is not None and listing.price_eur is not None and listing.price_eur < min_price:
        return f"price {listing.price_eur} € below min_price_eur {min_price} (auction teaser?)"

    max_price = filters.get("max_price_eur")
    if max_price is not None and listing.price_eur is not None and listing.price_eur > max_price:
        return f"price {listing.price_eur} € above max_price_eur {max_price}"

    max_ppm2 = filters.get("max_price_per_m2")
    ppm2 = listing.price_per_m2
    if max_ppm2 is not None and ppm2 is not None and ppm2 > max_ppm2:
        return f"price/m² {ppm2:g} € above max_price_per_m2 {max_ppm2}"

    min_rooms = filters.get("min_rooms")
    rooms = rooms_to_float(listing.rooms)
    if min_rooms is not None and rooms is not None:
        wanted = rooms_to_float(str(min_rooms)) or 0.0
        if rooms < wanted:
            return f"rooms {listing.rooms} below min_rooms {min_rooms}"

    max_floor = filters.get("max_floor")
    if max_floor is not None and listing.floor is not None and listing.floor > max_floor:
        return f"floor {listing.floor} above max_floor {max_floor}"

    if filters.get("exclude_ground_floor") and listing.floor == 0:
        return "ground floor excluded"

    if filters.get("require_balcony") and listing.balcony is False:
        return "balcony required but listing has none"

    allowed = filters.get("allowed_conditions") or []
    if (
        allowed
        and listing.condition is not Condition.UNKNOWN
        and listing.condition.value not in allowed
    ):
        return f"condition {listing.condition.value!r} not in allowed_conditions"

    for banned in filters.get("banned_streets") or []:
        if matches_place(banned, listing.street):
            return f"banned street {banned!r}"

    haystack = normalize_text(f"{listing.title} {listing.description_snippet}")
    for keyword in filters.get("banned_keywords") or []:
        if normalize_text(keyword) in haystack:
            return f"banned keyword {keyword!r}"

    return None


def score_listing(listing: Listing, rules: dict[str, Any]) -> tuple[int, list[str]]:
    """Soft preferences: return (score, human-readable breakdown)."""
    scoring = rules.get("scoring") or {}
    score = 0
    breakdown: list[str] = []

    for entry in scoring.get("preferred_streets") or []:
        if matches_place(entry["name"], listing.street):
            bonus = int(entry.get("bonus", 0))
            score += bonus
            breakdown.append(f"{bonus:+d} preferred street {entry['name']!r}")

    for entry in scoring.get("preferred_districts") or []:
        if matches_place(entry["name"], listing.district):
            bonus = int(entry.get("bonus", 0))
            score += bonus
            breakdown.append(f"{bonus:+d} preferred district {entry['name']!r}")

    condition_bonus = (scoring.get("condition_bonus") or {}).get(listing.condition.value)
    if condition_bonus:
        score += int(condition_bonus)
        breakdown.append(f"{int(condition_bonus):+d} condition {listing.condition.value}")

    balcony_bonus = scoring.get("balcony_bonus")
    if balcony_bonus and listing.balcony:
        score += int(balcony_bonus)
        breakdown.append(f"{int(balcony_bonus):+d} balcony")

    reference = scoring.get("price_per_m2_reference")
    ppm2 = listing.price_per_m2
    if reference and ppm2 is not None:
        pct_below = (reference - ppm2) / reference * 100
        bonus = max(-PRICE_REF_CAP, min(PRICE_REF_CAP, round(pct_below)))
        if bonus:
            score += bonus
            breakdown.append(
                f"{bonus:+d} price/m² {ppm2:.0f} € vs reference {reference} € "
                f"({pct_below:+.0f}%, capped at ±{PRICE_REF_CAP})"
            )

    return score, breakdown


def labels_for_score(score: int, rules: dict[str, Any]) -> list[str]:
    """Highest-threshold label whose ``min`` the score reaches, as a 1-element list."""
    entries = (rules.get("output") or {}).get("labels_by_score") or []
    eligible = [e for e in entries if score >= e.get("min", 0)]
    if not eligible:
        return []
    best = max(eligible, key=lambda e: e.get("min", 0))
    return [best["label"]]


def min_score_for_issue(rules: dict[str, Any]) -> int:
    value = (rules.get("output") or {}).get("min_score_for_issue")
    return int(value) if value is not None else 0
