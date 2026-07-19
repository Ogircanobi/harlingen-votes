"""PDF text extraction: pdfplumber -> pypdf -> (optional) OCR.

Extracted text is cached per fileId so re-runs (and slow OCR) aren't repeated.
OCR is opportunistic: if the `ocrmypdf` binary is on PATH we use it; otherwise
the page is flagged as needing OCR and we continue (the scraper still works).
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config

log = logging.getLogger("hvotes")

# A page yielding fewer than this many characters is treated as "empty" (likely
# a scanned image), triggering the next extraction tier.
MIN_CHARS_PER_PAGE = 20
_OCRMYPDF = shutil.which("ocrmypdf")


@dataclass
class PageText:
    page: int
    text: str


@dataclass
class Extraction:
    file_id: int
    method: str          # pdfplumber | pypdf | ocrmypdf | none
    ocr_used: bool
    ocr_needed: bool     # had little text and OCR was unavailable
    pages: list[PageText]

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)


def _text_cache_path(cfg: Config, file_id: int) -> Path:
    return cfg.text_dir / f"{file_id}.json"


def _load_cache(cfg: Config, file_id: int) -> Extraction | None:
    p = _text_cache_path(cfg, file_id)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        return Extraction(
            file_id=d["file_id"], method=d["method"], ocr_used=d["ocr_used"],
            ocr_needed=d["ocr_needed"],
            pages=[PageText(**pt) for pt in d["pages"]],
        )
    except Exception as exc:  # corrupt cache — re-extract
        log.warning("text cache unreadable for %s (%s); re-extracting", file_id, exc)
        return None


def _save_cache(cfg: Config, ex: Extraction) -> None:
    _text_cache_path(cfg, ex.file_id).write_text(
        json.dumps({**asdict(ex)}, ensure_ascii=False)
    )


def _extract_pdfplumber(path: Path) -> list[PageText]:
    import pdfplumber
    out: list[PageText] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            out.append(PageText(page=i, text=(page.extract_text() or "")))
    return out


def _extract_pypdf(path: Path) -> list[PageText]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return [PageText(page=i, text=(pg.extract_text() or ""))
            for i, pg in enumerate(reader.pages, start=1)]


def _looks_empty(pages: list[PageText]) -> bool:
    if not pages:
        return True
    total = sum(len(p.text.strip()) for p in pages)
    return total < MIN_CHARS_PER_PAGE * max(1, len(pages)) // 2


def _ocr(path: Path) -> list[PageText] | None:
    """Run ocrmypdf to a temp file and re-extract. Returns None if OCR fails."""
    if not _OCRMYPDF:
        return None
    with tempfile.TemporaryDirectory() as td:
        out_pdf = Path(td) / "ocr.pdf"
        try:
            subprocess.run(
                [_OCRMYPDF, "--skip-text", "--quiet", str(path), str(out_pdf)],
                check=True, capture_output=True, timeout=600,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ocrmypdf failed for %s: %s", path.name, exc)
            return None
        try:
            return _extract_pdfplumber(out_pdf)
        except Exception:
            return None


def extract(cfg: Config, file_id: int, pdf: Path) -> Extraction:
    cached = _load_cache(cfg, file_id)
    if cached is not None:
        # Re-attempt a cached "needs OCR" result if OCR has since become
        # available (e.g. ocrmypdf was installed after the first run).
        stale_ocr = (cached.ocr_needed and not cfg.skip_ocr
                     and _OCRMYPDF is not None)
        if not stale_ocr:
            return cached
        log.info("fileId=%s previously needed OCR; ocrmypdf now available — "
                 "re-extracting", file_id)

    method = "none"
    ocr_used = False
    ocr_needed = False
    pages: list[PageText] = []

    try:
        pages = _extract_pdfplumber(pdf)
        method = "pdfplumber"
    except Exception as exc:
        log.warning("pdfplumber failed for fileId=%s (%s); trying pypdf", file_id, exc)

    if _looks_empty(pages):
        try:
            alt = _extract_pypdf(pdf)
            if not _looks_empty(alt):
                pages, method = alt, "pypdf"
        except Exception as exc:
            log.debug("pypdf failed for fileId=%s: %s", file_id, exc)

    if _looks_empty(pages):
        if cfg.skip_ocr:
            ocr_needed = True
        else:
            ocr_pages = _ocr(pdf)
            if ocr_pages is not None and not _looks_empty(ocr_pages):
                pages, method, ocr_used = ocr_pages, "ocrmypdf", True
            else:
                ocr_needed = True
                if not _OCRMYPDF:
                    log.warning("fileId=%s looks scanned but ocrmypdf is not "
                                "installed — text not extracted", file_id)

    ex = Extraction(file_id=file_id, method=method, ocr_used=ocr_used,
                    ocr_needed=ocr_needed, pages=pages)
    _save_cache(cfg, ex)
    return ex
