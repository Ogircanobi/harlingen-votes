"""Configuration: defaults + the runtime Config object.

Everything here is overridable from the CLI (see cli.py). Defaults are chosen to
be polite to a small municipal server.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PORTAL_URL = "https://harlingentx.portal.civicclerk.com/"
API_BASE = "https://harlingentx.api.civicclerk.com/v1"

# Optional keyword search over scraped documents (--keywords / --keywords-file).
# Empty by default: this project indexes and downloads everything; the vote
# extraction step (votes.py) doesn't use keywords.
DEFAULT_KEYWORDS: list[str] = []

DEFAULT_START = date(2022, 1, 1)

# Polite request headers — look like the SPA, identify ourselves, give a contact.
DEFAULT_USER_AGENT = (
    "harlingen-votes/0.1 (public voting-records project; "
    "contact: set HVOTES_CONTACT env var)"
)


@dataclass
class Config:
    start: date
    end: date
    keywords: list[str]
    body: str | None = None            # categoryName filter, e.g. "City Commission"
    output_dir: Path = Path("output")
    cache_dir: Path = Path("cache")
    recon_dir: Path = Path("recon")
    delay: float = 1.5                 # seconds between requests
    timeout: float = 60.0
    user_agent: str = DEFAULT_USER_AGENT
    max_events: int | None = None      # cap for testing
    download: bool = True              # if False, build index only (no PDFs)
    skip_ocr: bool = False
    snippet_chars: int = 300
    auth_token: str | None = None      # only if the API ever requires one (env var)

    # Derived cache subdirs
    pdf_dir: Path = field(init=False)
    text_dir: Path = field(init=False)
    api_cache_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.pdf_dir = self.cache_dir / "pdfs"
        self.text_dir = self.cache_dir / "text"
        self.api_cache_dir = self.cache_dir / "api"

    def ensure_dirs(self) -> None:
        for d in (self.output_dir, self.cache_dir, self.recon_dir,
                  self.pdf_dir, self.text_dir, self.api_cache_dir):
            d.mkdir(parents=True, exist_ok=True)
