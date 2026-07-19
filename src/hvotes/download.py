"""Resumable PDF download cache."""
from __future__ import annotations

import logging
from pathlib import Path

from .api import CivicClerkClient
from .config import Config
from .models import PublishedFile

log = logging.getLogger("hvotes")


def pdf_path(cfg: Config, file_id: int) -> Path:
    return cfg.pdf_dir / f"{file_id}.pdf"


def ensure_pdf(client: CivicClerkClient, cfg: Config, pf: PublishedFile) -> Path | None:
    """Download pf to cache if not already present. Returns the path or None.

    Idempotent: an already-cached, non-empty PDF is left untouched (near-zero
    work on re-run).
    """
    dest = pdf_path(cfg, pf.file_id)
    if dest.exists() and dest.stat().st_size > 0:
        log.debug("cached PDF fileId=%s -> %s", pf.file_id, dest.name)
        return dest
    if not cfg.download:
        return None
    try:
        client.download_file(pf.file_id, dest)
        log.info("downloaded fileId=%s (%s) %.0f KB",
                 pf.file_id, pf.type, dest.stat().st_size / 1024)
        return dest
    except Exception as exc:  # noqa: BLE001 — log and continue per file
        log.error("download failed fileId=%s (%s): %s", pf.file_id, pf.type, exc)
        if dest.exists() and dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
        return None
