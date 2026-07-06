# Match history

One markdown file per month (`YYYY-MM.md`), appended automatically by the
crawler on every run - a human-readable log of every flat that passed the
filters, so you can browse the archive month by month instead of scrolling one
giant file.

Each row records the date it was logged, score, price (with the previous price
shown when it changed since last seen), area, rooms, address, condition, portal, and a
link to the listing.

These files are generated - **do not edit them by hand**; the crawler owns them.
The machine-readable dedupe memory lives in [`../state/seen.json`](../state/seen.json).
History starts accumulating from the first run after this feature shipped;
earlier matches were only stored as dedupe state and can't be back-filled with
full detail.
