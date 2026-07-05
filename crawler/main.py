"""Orchestrator: crawl every portal, filter, dedupe, report, persist state.

Exit codes: 0 = run completed (even if individual portals failed),
2 = rules.yaml invalid. Use ``--dry-run`` to parse and log without opening
Issues or touching the state file, ``--validate-rules`` to only check the
rules file.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import enrich
from . import rules as rules_mod
from . import state as state_mod
from .portals import all_portals
from .portals.base import PortalError
from .report import Reporter, ReportItem, issue_title

LOG = logging.getLogger("crawler")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reality-watch", description=__doc__)
    parser.add_argument("--rules", default=rules_mod.DEFAULT_RULES_PATH,
                        help="path to rules.yaml")
    parser.add_argument("--state", default=state_mod.DEFAULT_STATE_PATH,
                        help="path to state/seen.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and log only — no Issues, no state changes")
    parser.add_argument("--validate-rules", action="store_true",
                        help="validate rules.yaml and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def run(args: argparse.Namespace) -> int:
    try:
        rules = rules_mod.load_rules(args.rules)
    except rules_mod.RulesError as exc:
        LOG.error("%s", exc)
        return 2
    if args.validate_rules:
        LOG.info("%s is valid", args.rules)
        return 0

    dry_run = args.dry_run or _env_flag("DRY_RUN")
    enrich_enabled = _env_flag("ENRICH_ENABLED")
    state = state_mod.load_state(args.state)
    min_score = rules_mod.min_score_for_issue(rules)

    items: list[ReportItem] = []
    dropped = 0
    for portal in all_portals():
        try:
            listings = portal.fetch(rules)
            error = None
        except PortalError as exc:
            LOG.warning("portal %s failed: %s", portal.name, exc)
            listings, error = [], str(exc)
        except Exception as exc:
            LOG.exception("portal %s crashed", portal.name)
            listings, error = [], f"unexpected error: {exc}"
        if enrich_enabled:
            listings = [enrich.enrich(listing) for listing in listings]
        # Phantom cards (no price, area or rooms) are parser drift, not matches:
        # they must not become Issues, and a portal producing only phantoms is
        # as broken as one producing nothing — count only usable listings.
        usable = [listing for listing in listings if listing.has_usable_data]
        if len(usable) < len(listings):
            LOG.warning("portal %s: %d of %d cards had no extractable price/area/rooms "
                        "(parser drift?)", portal.name, len(listings) - len(usable), len(listings))
        streak = state_mod.record_portal_run(state, portal.name, len(usable), error)
        LOG.info("portal %s: %d usable listings (zero-streak: %d)",
                 portal.name, len(usable), streak)

        for listing in usable:
            status, previous = state_mod.classify_listing(state, listing)
            previous_price = previous.get("price_eur") if previous else None
            state_mod.remember_listing(state, listing)
            if status == "seen":
                continue
            reason = rules_mod.failing_filter(listing, rules)
            if reason:
                dropped += 1
                LOG.debug("dropped %s: %s", listing.id, reason)
                continue
            score, breakdown = rules_mod.score_listing(listing, rules)
            if score < min_score:
                dropped += 1
                LOG.debug("dropped %s: score %d below min_score_for_issue %d",
                          listing.id, score, min_score)
                continue
            labels = rules_mod.labels_for_score(score, rules)
            price_change = None
            if status == "price_change" and previous_price and listing.price_eur:
                labels = [*labels, "price-drop"]
                price_change = (previous_price, listing.price_eur)
            items.append(
                ReportItem(
                    listing=listing, score=score, breakdown=breakdown,
                    labels=labels, price_change=price_change,
                )
            )

    items.sort(key=lambda item: item.score, reverse=True)
    canaries = state_mod.portals_needing_canary(state)

    mode = (rules.get("output") or {}).get("mode", "digest")

    if dry_run:
        LOG.info("DRY RUN [%s mode] — %d matches would be reported, %d dropped by filters",
                 mode, len(items), dropped)
        for item in items:
            LOG.info("  would report: %s | labels=%s", issue_title(item), item.labels)
        for portal_name, streak in canaries:
            LOG.info("  would open scraper-broken issue for %s (streak %d)",
                     portal_name, streak)
        return 0

    reporter = Reporter()
    created = (
        reporter.report_digest(items)
        if mode == "digest"
        else reporter.report_matches(items)
    )
    for portal_name, streak in canaries:
        reporter.report_scraper_broken(portal_name, streak)

    pruned = state_mod.prune_state(state)
    state_mod.save_state(state, args.state)
    LOG.info("done: %d issues created, %d matches, %d dropped, %d stale entries pruned",
             created, len(items), dropped, pruned)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
