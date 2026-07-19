# harlingen-votes

The data pipeline and website behind **Harlingen City Commission Accountability** —
a public, facts-only record of every vote of the City Commission of Harlingen,
Texas, extracted from the City's own published minutes.

**Not affiliated with the City of Harlingen.** The official record is the
City's, at [harlingentx.portal.civicclerk.com](https://harlingentx.portal.civicclerk.com/).

## Why this exists

Harlingen publishes its meeting record as scanned paper documents, and it does
not publish minutes as standalone files: the approved minutes of a meeting
appear *inside the agenda packet of a later meeting*, usually two to four
months afterward. A resident who wants to know how their commissioner voted has
to know this quirk, find the right packet among hundreds of pages, and read a
scan. This project does that work once, for everyone, and shows its receipts.

## How it works

```
City portal (CivicClerk OData API)          NOTES.md documents the API
        │  hvotes scrape
        ▼
cache/pdfs + cache/text                     PDFs + extracted text (OCR when scanned)
        │  hvotes votes submit / fetch      Claude Batch API: locates embedded minutes,
        ▼                                   extracts motions w/ verbatim quote per vote
output/votes_raw.json
        │  hvotes votes build               name normalization, unanimous-vote
        ▼                                   attribution, quote verification
output/votes.json + people.json
        │
        ▼
static site                                 mockups/ shows the design
```

Every extracted vote carries provenance: the source packet's file id, a page
reference, a verbatim quote from the minutes (automatically matched back
against the source text), and links to both the original document and the
portal event page.

### Honesty rules, encoded in the data

- **`derived_ayes`** — most motions pass "unanimously" without individual votes
  being recorded. Individual positions are then attributed from the meeting's
  attendance roll and flagged as derived, never presented as roll-call votes.
- **`quote_verified`** — the verbatim quote was found in the source text.
- **`confidence`** — extraction confidence; poor scans are flagged, not hidden.
- Meetings whose minutes the City has not posted are listed as gaps, not omitted.
- Private citizens who appear in minutes (public commenters) are never
  republished — the site covers officials only.

## Setup

Requires Python 3.11+. For scanned documents you also want OCR tools:
`brew install ocrmypdf` (pulls tesseract + ghostscript).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
# 1. Scrape the portal: index events, download documents, extract text
hvotes scrape --body "City Commission"

# 2. Vote extraction (needs ANTHROPIC_API_KEY)
hvotes votes test --file-id <id>   # one live call — validate before batching
hvotes votes submit                # submit the batch (50% Batch API discount)
hvotes votes fetch                 # poll; writes output/votes_raw.json when done
hvotes votes build                 # -> output/votes.json + output/people.json

# utilities
hvotes bodies                      # list the portal's meeting bodies
hvotes scrape --help               # all flags (date range, delay, OCR, ...)
```

Environment variables:

- `ANTHROPIC_API_KEY` — required for `hvotes votes` extraction steps.
- `HVOTES_CONTACT` — contact info appended to the User-Agent (be a good citizen).
- `HVOTES_TOKEN` — bearer token, only if the portal API ever requires auth
  (it currently does not).

**Cost**: the full 2022→present backfill is roughly $16 of Claude Batch API
usage (~5M input tokens over ~80 agenda packets); keeping up with new meetings
afterward costs well under $1/month.

## Politeness & provenance

The portal is a public, anonymous OData API; its robots.txt disallows nothing.
The scraper identifies itself with a descriptive User-Agent, waits between
requests, backs off on errors, and caches everything so documents are fetched
once. The full reverse-engineering notes for the API are in [NOTES.md](NOTES.md),
produced by the scripts in `recon/`.

## What's not in this repo

`cache/` and `output/` (the document corpus and extraction results) are
excluded. Partly size — the corpus is ~1.7GB — but mostly deliberate: raw
minutes contain the names and home addresses of private citizens who spoke at
public comment. The published site redacts them; committing the raw corpus
would republish them. Run `hvotes scrape` to rebuild the corpus locally from
the City's portal.

## Status

- Scraper + extraction pipeline: built and tested (dry run).
- Full extraction backfill: pending.
- Site: design finalized in `mockups/harlingen-votes-mockups.html`
  (all names and votes in the mockup are illustrative samples).

## Corrections

If anything this project publishes misstates the City's minutes, open an issue
with a link to the row and the source document. Corrections are logged publicly.
