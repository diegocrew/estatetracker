# Fixtures

**These fixtures are synthetic.** The Slovak portals are not reachable from the
environment this project was developed in (datacenter egress is blocked), so
each file reproduces the portal's documented listing-card markup rather than a
byte-for-byte saved page. The parsers and these tests are therefore
**expected to need one update pass against real saved pages**: open a search
results page for each portal in a browser, save the HTML over the fixture file,
and adjust selectors in `crawler/portals/<portal>.py` until `pytest` is green
again.

`byty_search.html` goes a step further: unlike the other four, no real markup
for byty.sk was ever seen (not even historically), so its fixture is a
best-guess generic card structure, not a reproduction of anything observed.
Its parser (`crawler/portals/byty.py`) doesn't rely on specific CSS selectors
at all for exactly this reason - it harvests any link with a 5+ digit ID in
its path instead.

`yimba_projects.html` is the exception: it **is** a real capture, trimmed to one
card per project status, taken from
`https://www.yimba.sk/zoznam-projektov?type=Bývanie&district[]=downtown&district[]=ruzinov`.
Refresh it by saving that page and keeping a few `div.project-list-result`
cards.
