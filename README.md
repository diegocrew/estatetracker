# reality-watch

A crawler that runs twice daily via GitHub Actions, scrapes Slovak real estate
portals for **flats for sale in Bratislava**, filters them against a
user-editable rules file, deduplicates against previously seen listings, and
opens **one GitHub Issue per new matching listing**.

Portals: [nehnutelnosti.sk](https://www.nehnutelnosti.sk),
[topreality.sk](https://www.topreality.sk), [reality.sk](https://www.reality.sk),
[reality.bazos.sk](https://reality.bazos.sk).

No AI/LLM calls in this version - see [Future enrichment](#future-enrichment).

## How it works

1. `crawler/portals/*` fetch up to 3 search result pages per portal (politely:
   3–6 s randomized delays, browser User-Agent, `Accept-Language: sk`,
   single-threaded, robots.txt honored where fetchable).
2. Every listing is normalized into one schema (`crawler/models.py`); anything
   that can't be parsed becomes `None`, never a crash.
3. `state/seen.json` (committed back by the workflow) dedupes listings. A
   previously seen listing is re-reported when its price moves by **more than
   2%**, with a `price-drop` label and the old -> new price in the Issue.
   Entries not seen for 120 days are pruned.
   Every match is also appended to a human-readable **monthly archive** in
   [`history/`](history/) - one `history/YYYY-MM.md` file per month, each a
   table of price / area / rooms / address / link (see
   [`history/README.md`](history/README.md)). This is separate from
   `state/seen.json`: state is the crawler's memory, `history/` is for you to
   browse. Both are committed back by the workflow.
4. Hard filters from `rules.yaml` drop listings; soft preferences compute a
   score; one Issue per match is opened (max 20 per run - overflow goes into a
   single summary Issue).

## Editing rules.yaml

`rules.yaml` has four sections; validation fails fast with the exact offending
key, and you can check edits locally with
`python -m crawler.main --validate-rules`.

- **`search`** - city, list of districts (empty = all), transaction type.
- **`filters`** - *hard* limits: a listing violating any of these is dropped
  (`min_area_m2`, `max_area_m2`, `min_price_eur`, `max_price_eur`,
  `max_price_per_m2`, `min_rooms`,
  `max_floor`, `exclude_ground_floor`, `require_balcony`,
  `allowed_conditions`, `banned_streets`, `banned_keywords`). Keyword and
  street matching is case- and diacritics-insensitive ("Obchodna" matches
  "Obchodná"). **A missing/unparseable field never drops a listing** - only a
  confirmed violation does, with two opt-in exceptions:
  - `exclude_houses: true` drops houses and land (`rodinný dom`, `vila`,
    `pozemok`, …); anything that mentions a flat (`byt`, `garsónka`, …),
    including a flat *in* an apartment building, is kept.
  - `city_required: true` keeps only listings positively confirmed to be in
    `search.city` - the city name, one of your `search.districts`, or (for
    Bratislava) a borough must appear in the listing. This drops the
    surrounding villages ("20 min from Bratislava") that a text search pulls
    in. Trade-off: a genuine city flat whose locality the parser completely
    missed can be dropped too.
- **`scoring`** - *soft* preferences that affect the score, not inclusion:
  per-street and per-district bonuses, `preferred_keywords` (free-text terms
  matched in the title + description, e.g. `{name: "parkov", bonus: 20}` for
  parking/garage), condition bonuses
  (`novostavba`, `rekonstrukcia`, `povodny_stav`), a balcony bonus, and
  `price_per_m2_reference`.
- **`output`** - `mode`, `min_score_for_issue`, and the score -> label mapping.
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

Actions -> **crawl** -> *Run workflow*. Tick **dry_run** to parse and log without
opening Issues or committing state. Locally:

```bash
pip install -r requirements.txt
python -m crawler.main --dry-run --verbose
```

## Telegram notifications (optional)

Each run can push its digest to Telegram so new matches reach your phone. It is
off unless both secrets below are set; when unset the crawler simply skips it.

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts
   to get a **bot token** (looks like `123456789:AA...`).
2. Start a chat with your new bot and send it any message (a bot cannot message
   you first).
3. Get your **chat id**: open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the
   `chat.id` from the JSON (a positive number for a personal chat).
4. In the repo: **Settings -> Secrets and variables -> Actions -> New repository
   secret** and add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

That's it - the next run sends a message listing each new flat with its price,
area, rooms, address and link. Locally, export the same two env vars to test.

## Privacy note (public repo!)

`rules.yaml` reveals your budget and street preferences to anyone. To keep the
sensitive parts private, put a base64-encoded YAML fragment into a repository
Actions **variable** named `RULES_OVERRIDE_B64` - it is deep-merged over
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
  against saved fixtures in `tests/fixtures/` - those fixtures are currently
  *synthetic* (the portals were unreachable from the development environment),
  so expect one selector-update pass against real saved pages; the fixtures
  README explains how.
- Only ~3 pages per portal per run are fetched; a very fast market could push
  listings past page 3 between runs.

## AI enrichment (optional)

`crawler/enrich.py` sends each **new** listing's card text to Google **Gemini
2.5 Flash** and fills the fields deterministic parsing missed (price, street,
district, floor, condition, balcony) plus extras it can't get by regex
(parking, terrace, year built, is-new-development, red flags, and a short
summary shown in Telegram / the issue). Deterministic values are authoritative
- the model only fills gaps and never overwrites them.

It is **fail-open and off by default**: with no key it is a no-op, and any API
error or bad response returns the listing unchanged, so a bad AI day never
breaks a run. It also runs only on real runs (never on the push/dry-run smoke
tests) and only for new/price-changed listings, so API usage stays tiny.

To enable:

1. Create a **Gemini API key** (a Vertex AI *Express Mode* account-bound key
   works) and make sure the key is allowed to call the **Generative Language
   API** (enable it in the API Library).
2. Add it as the repository secret **`AI_KEY`**
   (Settings -> Secrets and variables -> Actions).
3. Optional repo *variables*: `AI_MODEL` (default `gemini-2.5-flash`) and
   `AI_API_BASE` (default `https://generativelanguage.googleapis.com/v1beta`;
   point this at the Vertex endpoint if your key only allows Vertex).

That's it - the next run enriches new listings. Cost at this volume (a handful
of new flats per run, twice daily) is a fraction of a cent.

Note: AI does **not** revive `nehnutelnosti.sk` - that portal renders listings
with JavaScript, so the fetched HTML has nothing to extract; that needs a
headless browser, which this version deliberately avoids.
