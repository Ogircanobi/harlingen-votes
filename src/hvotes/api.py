"""CivicClerk OData API client.

Polite by default: descriptive UA, configurable delay between requests,
exponential backoff on 429/5xx, no concurrency. See NOTES.md for the API map.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import API_BASE, Config
from .models import Body, Event, SchemaError

log = logging.getLogger("hvotes")


class RetryableHTTP(Exception):
    """429 or 5xx — worth retrying with backoff."""


class CivicClerkClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        headers = {
            "User-Agent": cfg.user_agent,
            "Accept": "application/json",
            "Origin": "https://harlingentx.portal.civicclerk.com",
            "Referer": "https://harlingentx.portal.civicclerk.com/",
        }
        if cfg.auth_token:
            headers["Authorization"] = f"Bearer {cfg.auth_token}"
        self._client = httpx.Client(headers=headers, timeout=cfg.timeout,
                                    follow_redirects=True)
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CivicClerkClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- low-level polite GET with backoff -------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.cfg.delay:
            time.sleep(self.cfg.delay - elapsed)

    @retry(
        retry=retry_if_exception_type((RetryableHTTP, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        self._throttle()
        try:
            resp = self._client.get(url, params=params)
        finally:
            self._last_request = time.monotonic()
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("HTTP %s on %s — backing off", resp.status_code, url)
            raise RetryableHTTP(f"{resp.status_code} {url}")
        resp.raise_for_status()
        return resp

    # --- bodies ----------------------------------------------------------
    def get_bodies(self) -> list[Body]:
        resp = self._get(f"{API_BASE}/EventCategories")
        data = resp.json()
        if "value" not in data:
            raise SchemaError("EventCategories: no 'value' array")
        return [Body.parse(o, self.cfg.recon_dir) for o in data["value"]]

    # --- events (paged via @odata.nextLink) ------------------------------
    def iter_events(self, start: date, end: date,
                    body: str | None = None) -> Iterator[Event]:
        """Yield every published event in [start, end), following nextLink.

        The server caps pages at 15 and returns @odata.nextLink; we follow it
        until absent. Date literals are unquoted (see NOTES.md).
        """
        filt = (f"startDateTime ge {start.isoformat()} "
                f"and startDateTime lt {end.isoformat()}")
        if body:
            filt += f" and categoryName eq '{body}'"
        params = {
            "$filter": filt,
            "$orderby": "startDateTime desc, eventName desc",
            "$count": "true",
        }

        url: str | None = f"{API_BASE}/Events"
        page = 0
        yielded = 0
        total: int | None = None
        while url:
            resp = self._get(url, params=params)
            data = resp.json()
            if total is None:
                total = data.get("@odata.count")
                if total is not None:
                    log.info("Server reports %s matching events", total)
            if "value" not in data:
                raise SchemaError("Events: no 'value' array")
            page += 1
            for obj in data["value"]:
                yield Event.parse(obj, self.cfg.recon_dir)
                yielded += 1
                if self.cfg.max_events and yielded >= self.cfg.max_events:
                    log.info("Reached --max-events=%s; stopping pagination",
                             self.cfg.max_events)
                    return
            log.info("Fetched events page %d (%d so far)", page, yielded)
            # nextLink is an absolute URL that already encodes $skip; params
            # must not be re-applied to it.
            url = data.get("@odata.nextLink")
            params = None

    # --- file download ---------------------------------------------------
    def download_file(self, file_id: int, dest: Path) -> bool:
        """Stream a published file's PDF to dest. Returns True if written."""
        url = (f"{API_BASE}/Meetings/GetMeetingFileStream"
               f"(fileId={file_id},plainText=false)")
        resp = self._get(url)
        content = resp.content
        if not content[:5].startswith(b"%PDF"):
            ctype = resp.headers.get("content-type", "")
            log.warning("file %s: not a PDF (content-type=%s, %d bytes) — saving anyway",
                        file_id, ctype, len(content))
        dest.write_bytes(content)
        return True
