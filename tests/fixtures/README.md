# Fixtures

**These fixtures are synthetic.** The Slovak portals are not reachable from the
environment this project was developed in (datacenter egress is blocked), so
each file reproduces the portal's documented listing-card markup rather than a
byte-for-byte saved page. The parsers and these tests are therefore
**expected to need one update pass against real saved pages**: open a search
results page for each portal in a browser, save the HTML over the fixture file,
and adjust selectors in `crawler/portals/<portal>.py` until `pytest` is green
again.
