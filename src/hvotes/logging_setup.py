"""Structured logging to stdout + a logfile."""
from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(output_dir: Path, verbose: bool = False) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logfile = output_dir / "hvotes.log"

    logger = logging.getLogger("hvotes")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()  # idempotent across re-imports

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Quiet noisy libraries.
    for noisy in ("httpx", "httpcore", "pdfminer", "pdfplumber"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logger
