"""Output writers: meetings.json, meetings.csv, keyword_hits.csv, report.md."""
from __future__ import annotations

import csv
import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import API_BASE, Config

log = logging.getLogger("hvotes")


def file_stream_url(file_id: int) -> str:
    return (f"{API_BASE}/Meetings/GetMeetingFileStream"
            f"(fileId={file_id},plainText=false)")


@dataclass
class FileRecord:
    file_id: int
    type: str
    name: str
    source_url: str
    portal_url: str
    cached: bool
    extract_method: str
    ocr_needed: bool
    n_pages: int
    n_chars: int


@dataclass
class MeetingRecord:
    event_id: int
    name: str
    body: str
    date: str
    meeting_type: str | None
    has_agenda: bool
    portal_url: str = ""
    files: list[FileRecord] = field(default_factory=list)


@dataclass
class HitRecord:
    keyword: str
    date: str
    body: str
    event_id: int
    event_name: str
    doc_type: str
    file_name: str
    location: str
    page: int | None
    snippet: str
    source_url: str
    portal_url: str


# --- writers -------------------------------------------------------------
def write_meetings(cfg: Config, meetings: list[MeetingRecord]) -> None:
    # JSON — full structured index
    (cfg.output_dir / "meetings.json").write_text(
        json.dumps([asdict(m) for m in meetings], indent=2, ensure_ascii=False)
    )
    # CSV — one row per file (events with no files still get a row)
    path = cfg.output_dir / "meetings.csv"
    cols = ["event_id", "date", "body", "event_name", "meeting_type",
            "has_agenda", "file_id", "doc_type", "file_name",
            "extract_method", "ocr_needed", "n_pages", "n_chars",
            "source_url", "portal_url"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for m in meetings:
            if not m.files:
                w.writerow({"event_id": m.event_id, "date": m.date, "body": m.body,
                            "event_name": m.name, "meeting_type": m.meeting_type or "",
                            "has_agenda": m.has_agenda, "portal_url": m.portal_url})
                continue
            for f in m.files:
                w.writerow({
                    "event_id": m.event_id, "date": m.date, "body": m.body,
                    "event_name": m.name, "meeting_type": m.meeting_type or "",
                    "has_agenda": m.has_agenda, "file_id": f.file_id,
                    "doc_type": f.type, "file_name": f.name,
                    "extract_method": f.extract_method, "ocr_needed": f.ocr_needed,
                    "n_pages": f.n_pages, "n_chars": f.n_chars,
                    "source_url": f.source_url, "portal_url": f.portal_url,
                })


def write_hits(cfg: Config, hits: list[HitRecord]) -> None:
    path = cfg.output_dir / "keyword_hits.csv"
    cols = ["keyword", "date", "body", "event_id", "event_name", "doc_type",
            "file_name", "location", "page", "snippet", "source_url",
            "portal_url"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for h in hits:
            row = asdict(h)
            row["page"] = "" if h.page is None else h.page
            w.writerow(row)


def write_report(cfg: Config, meetings: list[MeetingRecord],
                 hits: list[HitRecord]) -> None:
    by_event: dict[int, list[HitRecord]] = defaultdict(list)
    for h in hits:
        by_event[h.event_id].append(h)

    meetings_sorted = sorted(meetings, key=lambda m: m.date, reverse=True)
    hit_meetings = [m for m in meetings_sorted if by_event.get(m.event_id)]

    kw_counts = Counter(h.keyword for h in hits)
    body_event_counts = Counter(m.body for m in meetings)
    body_file_counts = Counter(f.type for m in meetings for f in m.files)

    lines: list[str] = []
    lines.append("# Harlingen Portal — Keyword Search Report")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}_  ")
    lines.append(f"_Date range: {cfg.start.isoformat()} → {cfg.end.isoformat()}_  ")
    if cfg.body:
        lines.append(f"_Body filter: {cfg.body}_  ")
    lines.append("")
    lines.append(f"- Meetings indexed: **{len(meetings)}**")
    lines.append(f"- Meetings with keyword hits: **{len(hit_meetings)}**")
    lines.append(f"- Total keyword hits: **{len(hits)}**")
    lines.append(f"- Keywords searched: {', '.join('`'+k+'`' for k in cfg.keywords)}")
    lines.append("")

    if kw_counts:
        lines.append("## Hits by keyword")
        lines.append("")
        lines.append("| Keyword | Hits |")
        lines.append("|---|---|")
        for kw, n in kw_counts.most_common():
            lines.append(f"| {kw} | {n} |")
        lines.append("")

    lines.append("## Documents by type")
    lines.append("")
    lines.append("| Document type | Count |")
    lines.append("|---|---|")
    for t, n in body_file_counts.most_common():
        lines.append(f"| {t} | {n} |")
    lines.append("")

    lines.append("## Matches by meeting (newest first)")
    lines.append("")
    if not hit_meetings:
        lines.append("_No keyword matches found._")
        lines.append("")
    for m in hit_meetings:
        lines.append(f"### {m.date} — {m.body}: {m.name}")
        lines.append("")
        # group this meeting's hits by file
        per_file: dict[str, list[HitRecord]] = defaultdict(list)
        for h in by_event[m.event_id]:
            per_file[f"{h.doc_type} — {h.file_name}".strip(" —")].append(h)
        for label, fhits in per_file.items():
            url = fhits[0].source_url
            portal = fhits[0].portal_url
            kws = ", ".join(sorted({h.keyword for h in fhits}))
            portal_link = f" · [portal]({portal})" if portal else ""
            lines.append(f"- **{label}** ([download]({url}){portal_link}) — "
                         f"{len(fhits)} hit(s): {kws}")
            for h in fhits[:8]:
                loc = f"p.{h.page}" if h.page else h.location
                lines.append(f"    - `{h.keyword}` ({loc}): {h.snippet}")
            if len(fhits) > 8:
                lines.append(f"    - … and {len(fhits) - 8} more")
        lines.append("")

    (cfg.output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def print_summary(cfg: Config, meetings: list[MeetingRecord],
                  hits: list[HitRecord]) -> None:
    """Per-body event/document counts for sanity-checking against the portal UI."""
    ev_by_body: Counter = Counter(m.body for m in meetings)
    files_by_body: Counter = Counter()
    for m in meetings:
        files_by_body[m.body] += len(m.files)
    ocr_needed = sum(1 for m in meetings for f in m.files if f.ocr_needed)

    print("\n" + "=" * 60)
    print("PER-BODY TOTALS (sanity-check against the portal UI)")
    print("=" * 60)
    print(f"{'Body':<45}{'events':>7}{'docs':>7}")
    print("-" * 60)
    for body, n in ev_by_body.most_common():
        print(f"{body[:44]:<45}{n:>7}{files_by_body[body]:>7}")
    print("-" * 60)
    print(f"{'TOTAL':<45}{sum(ev_by_body.values()):>7}{sum(files_by_body.values()):>7}")
    print("=" * 60)
    print(f"Keyword hits: {len(hits)}   "
          f"Meetings with hits: {len({h.event_id for h in hits})}")
    if ocr_needed:
        print(f"⚠ {ocr_needed} document(s) look scanned and were NOT text-extracted "
              f"(install tesseract+ocrmypdf, or run without --skip-ocr).")
    print(f"Outputs written to: {cfg.output_dir}/")
