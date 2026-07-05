# reality-watch

A crawler that runs twice daily via GitHub Actions, scrapes Slovak real estate
portals for **flats for sale in Bratislava**, filters them against a
user-editable rules file, deduplicates against previously seen listings, and
opens **one GitHub Issue per new matching listing**.

Portals: [nehnutelnosti.sk](https://www.nehnutelnosti.sk),
[topreality.sk](https://www.topreality.sk), [reality.sk](https://www.reality.sk),
[reality.bazos.sk](https://reality.bazos.sk).

No AI/LLM calls in this version — see [Future enrichment](#future-enrichment).

## How it works

1. `crawler/portals/*` fetch up to 3 search result pages per portal (politely:
   3–6 s randomized delays, browser User-Agent, `Accept-Language: sk`,
   single-threaded, robots.txt honored where fetchable).
2. Every listing is normalized into one schema (`crawler/models.py`); anything
   that can't be parsed becomes `None`, never a crash.
3. `state/seen.json` (committed back by the workflow) dedupes listings. A
   previously seen listing is re-reported when its price moves by **more than
   2%**, with a `price-drop` label and the old → new price in the Issue.
   Entries not seen for 120 days are pruned.
4. Hard filters from `rules.yaml` drop listings; soft preferences compute a
   score; one Issue per match is opened (max 20 per run — overflow goes into a
   single summary Issue).

## Editing rules.yaml

`rules.yaml` has four sections; validation fails fast with the exact offending
key, and you can check edits locally with
`python -m crawler.main --validate-rules`.

- **`search`** — city, list of districts (empty = all), transaction type.
- **`filters`** — *hard* limits: a listing violating any of these is dropped
  (`min_area_m2`, `min_price_eur`, `max_price_eur`, `max_price_per_m2`, `min_rooms`,
  `max_floor`, `exclude_ground_floor`, `require_balcony`,
  `allowed_conditions`, `banned_streets`, `banned_keywords`). Keyword and
  street matching is case- and diacritics-insensitive ("Obchodna" matches
  "Obchodná"). **A missing/unparseable field never drops a listing** — only a
  confirmed violation does, with two opt-in exceptions:
  - `exclude_houses: true` drops houses and land (`rodinný dom`, `vila`,
    `pozemok`, …); anything that mentions a flat (`byt`, `garsónka`, …),
    including a flat *in* an apartment building, is kept.
  - `city_required: true` keeps only listings positively confirmed to be in
    `search.city` — the city name, one of your `search.districts`, or (for
    Bratislava) a borough must appear in the listing. This drops the
    surrounding villages ("20 min from Bratislava") that a text search pulls
    in. Trade-off: a genuine city flat whose locality the parser completely
    missed can be dropped too.
- **`scoring`** — *soft* preferences that affect the score, not inclusion:
  per-street and per-district bonuses, `preferred_keywords` (free-text terms
  matched in the title + description, e.g. `{name: "parkov", bonus: 20}` for
  parking/garage), condition bonuses
  (`novostavba`, `rekonstrukcia`, `povodny_stav`), a balcony bonus, and
  `price_per_m2_reference`.
- **`output`** — `mode`, `min_score_for_issue`, and the score → label mapping.
  `mode: digest` (default) collects a whole run's new matches into **one**
  GitHub Issue with a price / area / rooms / address table; `mode:
  issue_per_listing` opens one Issue per flat (capped at 20/run + an overflow
  summary). A run with no new matches opens nothing.

### How scoring works

The score starts at 0 and adds every matching bonus. The price component gives
**+1 point per % below `price_per_m2_reference`** (and −1 per % above), capped
at ±25. The Issue gets the label with the highest `min` threshold the score
reaches (`hot` ≥ 40, `interesting` ≥ 20, `match` ≥ 0 by default), and the Issue
body shows the full breakdown of which rules contributed.

## Manual runs

Actions → **crawl** → *Run workflow*. Tick **dry_run** to parse and log without
opening Issues or committing state. Locally:

```bash
pip install -r requirements.txt
python -m crawler.main --dry-run --verbose
```

## Privacy note (public repo!)

`rules.yaml` reveals your budget and street preferences to anyone. To keep the
sensitive parts private, put a base64-encoded YAML fragment into a repository
Actions **variable** named `RULES_OVERRIDE_B64` — it is deep-merged over
`rules.yaml` at runtime (already wired into the workflow):

```bash
base64 -w0 <<'EOF'
filters:
  max_price_eur: 300000
scoring:
  preferred_streets:
    - {name: "Moja tajná ulica", bonus: 40}
EOF
```

## Known limitations

- **Anti-bot blocking**: portals may block GitHub Actions' datacenter IPs
  (Cloudflare challenges, HTTP 403/429). A blocked portal never fails the run;
  it's recorded in `state/seen.json` under `portal_health`, and after **3
  consecutive zero-listing runs** a single `scraper-broken` maintenance Issue
  is opened per portal (deduplicated while one is already open).
- **HTML drift**: the portals change markup regularly. Parsers are written
  against saved fixtures in `tests/fixtures/` — those fixtures are currently
  *synthetic* (the portals were unreachable from the development environment),
  so expect one selector-update pass against real saved pages; the fixtures
  README explains how.
- Only ~3 pages per portal per run are fetched; a very fast market could push
  listings past page 3 between runs.

## Future enrichment

`crawler/enrich.py` is a stub for a Vertex AI Gemini step that will extract
`condition`, `floor`, `balcony`, orientation, and red flags from the listing
text when deterministic parsing returned `None`. It is wired into `main.py`
behind the `ENRICH_ENABLED` env var (default off) and currently returns the
listing unchanged — swapping in the real implementation requires no
refactoring.
