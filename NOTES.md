# NOTES.md — Discovered API (Harlingen, TX CivicClerk portal)

Phase 1 reconnaissance output. Everything below was **verified live** via the
recon scripts in `recon/`, not assumed. Re-run `recon/recon.py` and
`recon/recon_event.py` to refresh the raw network logs.

## Summary

- **Portal (SPA):** `https://harlingentx.portal.civicclerk.com/` — a JavaScript app; static HTML is empty. Do not parse it.
- **Backend API:** `https://harlingentx.api.civicclerk.com/v1` — an **OData v4 REST API** (the sibling `*.api.civicclerk.com` host the prompt hinted at, confirmed).
- **Auth:** none required. Anonymous requests succeed. (There is an identity-server config endpoint, but public reads need no token.)
- **robots.txt:** `https://harlingentx.portal.civicclerk.com/robots.txt` → `User-agent: *` with **no `Disallow`** lines (nothing disallowed). The API host returns 404 for `/robots.txt` (no rules).
- **Corpus size:** **582 events** between `2022-01-01` and `2026-06-23`.

## Endpoints

### 1. Bodies / committees — `GET /v1/EventCategories`
Returns the list of meeting bodies. No params.

```json
{ "@odata.context": ".../$metadata#EventCategories",
  "value": [
    {"id": 26, "categoryDesc": "City Commission", "sortOrder": 1, "isPublic": 1, "parentId": -1},
    {"id": 29, "categoryDesc": "Planning & Zoning", "sortOrder": 4, "isPublic": 1, "parentId": -1},
    {"id": 34, "categoryDesc": "Development Corporation of Harlingen, Inc.", "...": "..."}
  ] }
```
Bodies seen include: General (24), City Commission (26), Airport Board (27),
Visit Harlingen (28), Planning & Zoning (29), Zoning Board of Adjustments (30),
Parks & Recreations (31), Harlingen Public Library (32), Harlingen Waterworks
System Utility Board of Trustees (33), Development Corporation of Harlingen (34), …
Use `categoryDesc` to match against the event's `categoryName`.

### 2. Events / meetings — `GET /v1/Events`
The core endpoint. Returns events **with their documents inline** (no separate
document call needed — see `publishedFiles` below).

**Query params (OData):**
| Param | Example | Notes |
|-------|---------|-------|
| `$filter` | `startDateTime ge 2022-01-01 and startDateTime lt 2026-06-23` | Date literals are **unquoted**. Combine with `and`. `categoryName eq 'City Commission'` also works. |
| `$orderby` | `startDateTime desc, eventName desc` | |
| `$top` / `$skip` | `$top=15&$skip=30` | Manual paging. |
| `$count` | `true` | Adds `@odata.count` (total matching). Works with simple filters. |

**⚠ Page size is capped at 15.** A request without `$top` returns at most 15
items plus an **`@odata.nextLink`**. To get all events you MUST page: either
follow `@odata.nextLink` until absent, or loop `$skip` in steps of 15 (or set an
explicit `$top` — confirm the server honors values >15 before relying on it; the
default cap is 15).

**Event object — relevant fields** (full object has ~90 fields; `recon/sample_event_674.json` has a trimmed real example):

| Field | Meaning |
|-------|---------|
| `id` | Event id (e.g. 674) |
| `eventName` | e.g. "City Commission Regular Meeting" |
| `eventDate` / `startDateTime` | ISO 8601 UTC (`2026-06-17T17:30:00Z`) |
| `isPublished` | `"Published"` / other |
| `categoryName`, `categoryId`, `eventCategoryName` | Body/committee |
| `meetingTypeName` | Often `null` here |
| `hasAgenda`, `hasMedia` | Booleans |
| `agendaId`, `agendaName` | Agenda metadata |
| `agendaFile` | `{agendaId, fileName, dateUploaded, createdBy}` — **often empty** (`fileName: null`); the real files live in `publishedFiles`. |
| `minutesFile` | `{minutesId, eventId, fileName, createdOn, createdBy}` — **often empty** in samples. |
| `publishedFiles[]` | **The documents.** See below. |

**`publishedFiles[]` element:**
```json
{ "fileId": 1516, "type": "Agenda", "name": "Agenda Final",
  "url": "stream/HARLINGENTX/96aee894-...-9ecf5658d090.pdf",
  "fileType": 1, "sort": 1, "publishOn": "2026-06-11T16:14:49.15Z",
  "id": 0, "publishTo": null, "publishBy": null, "streamUrl": null }
```
- `type` (string, human label) ↔ `fileType` (int) seen so far:
  `Agenda` = 1, `Agenda Packet` = 2, `Other` = 7, `Notice` = 5.
- **`fileId` is the download key** (NOT `url`). The `url` field is an internal
  blob path and is **not directly fetchable** (404 on every host tried).

### 3. Document download — `GET /v1/Meetings/GetMeetingFileStream(fileId={fileId},plainText=false)`
Streams the PDF. **Verified:** `Content-Type: application/pdf`, body starts with
`%PDF`. Example: `.../v1/Meetings/GetMeetingFileStream(fileId=1516,plainText=false)`
→ 200, 171 KB PDF. (`plainText=true` likely returns extracted text — to test in
Phase 3; default to downloading the PDF and extracting locally for reliability.)

### 4. Calendar helper (optional) — `GET /v1/Events/GetDaysInMonthWithEvents(date='YYYY-MM-DD',categories=[])`
Returns the days in a month that have events (`["06-02-2026", ...]`). Used by the
SPA's calendar; not needed for bulk scraping but handy for cross-checking.

### 5. Settings (not needed for scraping)
- `GET /v1/Settings/GetIdentityServerConfiguration` → `{clientId, apiUrl}` (auth server; unused for public reads).
- `GET /v1/Settings/GetPublicPortalCustomizations` → portal name/logo/etc.

## Required request headers
Anonymous works, but send these to look like the SPA and stay polite:
```
User-Agent: <descriptive UA with contact>
Accept: application/json
Origin: https://harlingentx.portal.civicclerk.com
Referer: https://harlingentx.portal.civicclerk.com/
```

## Quirks & gotchas (important for the scraper)
1. **Keyed entity access hangs.** `GET /v1/Events(674)` (OData key syntax) reads
   time out. Use `GET /v1/Events?$filter=id eq 674` instead.
2. **No nested-collection filtering.** `$filter=publishedFiles/any(f: f/type eq 'Minutes')`
   returns **HTTP 500**. Filter file types **client-side** after fetching events.
3. **Page cap = 15** with `@odata.nextLink` (see §2). Easy to under-count if ignored.
4. **`agendaFile` / `minutesFile` are usually empty** — treat `publishedFiles[]`
   as the source of truth for downloadable documents.
5. **A dedicated "Minutes" file type was NOT observed** in the sampled events
   (types seen: Agenda, Agenda Packet, Notice, Other). Minutes may be published
   under `Other`, may be absent, or appear only in older events. **Decision:**
   the scraper downloads and keyword-searches **every** `publishedFiles[]` entry
   regardless of `type`, recording each file's `type` — so nothing is missed and
   we report honestly what document types actually exist per body.
6. **Date literals are unquoted** in `$filter` (`startDateTime ge 2022-01-01`).

## Raw artifacts
- `recon/network_log.json` — initial-load XHR/fetch capture.
- `recon/network_log_event.json` — capture while opening an event (SPA route `/event/{id}/files`).
- `recon/sample_event_674.json` — trimmed real event with two published files.
