"""Typed models + fail-loud schema validation.

If an API payload doesn't match the documented shape, we raise SchemaError and
dump the offending payload to recon/ for inspection rather than silently skipping.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SchemaError(Exception):
    """Raised when an API response doesn't match the documented schema."""


def _dump_bad_payload(recon_dir: Path, label: str, payload: Any) -> Path:
    recon_dir.mkdir(parents=True, exist_ok=True)
    path = recon_dir / f"bad_payload_{label}_{int(time.time())}.json"
    try:
        path.write_text(json.dumps(payload, indent=2, default=str))
    except Exception:
        path.write_text(repr(payload))
    return path


def _require(obj: dict, keys: tuple[str, ...], label: str, recon_dir: Path) -> None:
    if not isinstance(obj, dict):
        p = _dump_bad_payload(recon_dir, label, obj)
        raise SchemaError(f"{label}: expected object, got {type(obj).__name__}. Saved {p}")
    missing = [k for k in keys if k not in obj]
    if missing:
        p = _dump_bad_payload(recon_dir, label, obj)
        raise SchemaError(f"{label}: missing keys {missing}. Saved {p}")


@dataclass
class Body:
    id: int
    name: str

    @classmethod
    def parse(cls, obj: dict, recon_dir: Path) -> "Body":
        _require(obj, ("id", "categoryDesc"), "EventCategory", recon_dir)
        return cls(id=int(obj["id"]), name=str(obj["categoryDesc"]))


@dataclass
class PublishedFile:
    file_id: int
    type: str            # "Agenda", "Agenda Packet", "Minutes", "Notice", "Other"
    name: str
    file_type: int
    publish_on: str | None = None

    @classmethod
    def parse(cls, obj: dict, recon_dir: Path) -> "PublishedFile":
        _require(obj, ("fileId", "type"), "PublishedFile", recon_dir)
        return cls(
            file_id=int(obj["fileId"]),
            type=str(obj.get("type") or "Unknown"),
            name=str(obj.get("name") or ""),
            file_type=int(obj.get("fileType") or 0),
            publish_on=obj.get("publishOn"),
        )


@dataclass
class Event:
    id: int
    name: str
    body: str
    start: datetime
    meeting_type: str | None
    has_agenda: bool
    files: list[PublishedFile] = field(default_factory=list)
    raw_keywords: str = ""

    @classmethod
    def parse(cls, obj: dict, recon_dir: Path) -> "Event":
        _require(obj, ("id", "eventName", "startDateTime"), "Event", recon_dir)
        files = [
            PublishedFile.parse(f, recon_dir)
            for f in (obj.get("publishedFiles") or [])
        ]
        return cls(
            id=int(obj["id"]),
            name=str(obj["eventName"]),
            body=str(obj.get("categoryName") or obj.get("eventCategoryName") or "Unknown"),
            start=_parse_dt(obj["startDateTime"]),
            meeting_type=obj.get("meetingTypeName"),
            has_agenda=bool(obj.get("hasAgenda")),
            files=files,
            raw_keywords=str(obj.get("keywords") or ""),
        )

    @property
    def date_str(self) -> str:
        return self.start.date().isoformat()


def _parse_dt(value: str) -> datetime:
    # API returns e.g. "2026-06-17T17:30:00Z"
    s = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
