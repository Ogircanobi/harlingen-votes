"""End-to-end run: discover events -> download docs -> extract -> search -> report."""
from __future__ import annotations

import json
import logging

from .api import CivicClerkClient
from .config import PORTAL_URL, Config
from .download import ensure_pdf
from .extract import extract
from .report import (
    FileRecord,
    HitRecord,
    MeetingRecord,
    file_stream_url,
    print_summary,
    write_hits,
    write_meetings,
    write_report,
)
from .search import compile_patterns, search_extraction, search_metadata

log = logging.getLogger("hvotes")


def run(cfg: Config) -> tuple[list[MeetingRecord], list[HitRecord]]:
    cfg.ensure_dirs()
    patterns = compile_patterns(cfg.keywords)
    meetings: list[MeetingRecord] = []
    hits: list[HitRecord] = []

    with CivicClerkClient(cfg) as client:
        bodies = client.get_bodies()
        (cfg.api_cache_dir / "bodies.json").write_text(
            json.dumps([{"id": b.id, "name": b.name} for b in bodies], indent=2)
        )
        log.info("Discovered %d meeting bodies", len(bodies))

        log.info("Fetching events %s → %s%s", cfg.start, cfg.end,
                 f" (body={cfg.body})" if cfg.body else "")
        events = list(client.iter_events(cfg.start, cfg.end, cfg.body))
        log.info("Collected %d events", len(events))
        # Snapshot the API response for fail-loud inspection / debugging.
        (cfg.api_cache_dir / "events_raw.json").write_text(
            json.dumps([{
                "id": e.id, "name": e.name, "body": e.body,
                "date": e.date_str, "files": [f.file_id for f in e.files],
            } for e in events], indent=2)
        )

        for ev in events:
            event_url = f"{PORTAL_URL}event/{ev.id}/files"

            # 1) metadata / title search
            for mh in search_metadata(ev.name, ev.raw_keywords, patterns,
                                      cfg.snippet_chars):
                hits.append(HitRecord(
                    keyword=mh.keyword, date=ev.date_str, body=ev.body,
                    event_id=ev.id, event_name=ev.name, doc_type="(metadata)",
                    file_name="", location=mh.location, page=None,
                    snippet=mh.snippet, source_url=event_url,
                    portal_url=event_url))

            # 2) per-document download + extract + search
            file_records: list[FileRecord] = []
            for pf in ev.files:
                src = file_stream_url(pf.file_id)
                path = ensure_pdf(client, cfg, pf)
                method, ocr_needed, n_pages, n_chars = "not-downloaded", False, 0, 0
                cached = path is not None
                if path is not None:
                    ex = extract(cfg, pf.file_id, path)
                    method, ocr_needed = ex.method, ex.ocr_needed
                    n_pages, n_chars = len(ex.pages), ex.total_chars
                    for dh in search_extraction(ex, patterns, cfg.snippet_chars):
                        hits.append(HitRecord(
                            keyword=dh.keyword, date=ev.date_str, body=ev.body,
                            event_id=ev.id, event_name=ev.name, doc_type=pf.type,
                            file_name=pf.name, location="document", page=dh.page,
                            snippet=dh.snippet, source_url=src,
                            portal_url=event_url))
                file_records.append(FileRecord(
                    file_id=pf.file_id, type=pf.type, name=pf.name, source_url=src,
                    portal_url=event_url,
                    cached=cached, extract_method=method, ocr_needed=ocr_needed,
                    n_pages=n_pages, n_chars=n_chars))

            meetings.append(MeetingRecord(
                event_id=ev.id, name=ev.name, body=ev.body, date=ev.date_str,
                meeting_type=ev.meeting_type, has_agenda=ev.has_agenda,
                portal_url=event_url, files=file_records))

    write_meetings(cfg, meetings)
    write_hits(cfg, hits)
    write_report(cfg, meetings, hits)
    print_summary(cfg, meetings, hits)
    return meetings, hits
