"""Typer CLI."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer

from .config import DEFAULT_KEYWORDS, DEFAULT_START, DEFAULT_USER_AGENT, Config
from .logging_setup import setup_logging

app = typer.Typer(add_completion=False, help=__doc__,
                  context_settings={"help_option_names": ["-h", "--help"]})


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _load_keywords(keywords: Optional[str], keywords_file: Optional[Path]) -> list[str]:
    if keywords_file:
        lines = keywords_file.read_text().splitlines()
        kws = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
        return kws
    if keywords:
        return [k.strip() for k in keywords.split(",") if k.strip()]
    return list(DEFAULT_KEYWORDS)


@app.command()
def scrape(
    start: str = typer.Option(DEFAULT_START.isoformat(), help="Start date YYYY-MM-DD (inclusive)."),
    end: Optional[str] = typer.Option(None, help="End date YYYY-MM-DD (exclusive). Default: today."),
    body: Optional[str] = typer.Option(None, help="Filter to one body, e.g. 'City Commission'."),
    keywords: Optional[str] = typer.Option(None, help="Comma-separated keyword list (overrides defaults)."),
    keywords_file: Optional[Path] = typer.Option(None, help="File with one keyword/phrase per line."),
    output_dir: Path = typer.Option(Path("output"), help="Where to write outputs."),
    cache_dir: Path = typer.Option(Path("cache"), help="Where to cache PDFs + extracted text."),
    delay: float = typer.Option(1.5, help="Seconds between requests (politeness)."),
    max_events: Optional[int] = typer.Option(None, help="Cap events processed (testing)."),
    no_download: bool = typer.Option(False, "--no-download", help="Index only; skip PDF downloads."),
    skip_ocr: bool = typer.Option(False, "--skip-ocr", help="Don't OCR scanned PDFs."),
    user_agent: str = typer.Option(DEFAULT_USER_AGENT, help="HTTP User-Agent."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Debug logging to stdout."),
) -> None:
    """Scrape the Harlingen CivicClerk portal and keyword-search every document."""
    from .pipeline import run  # deferred import so `--help` is fast

    end_date = _parse_date(end) if end else date.today()
    contact = os.environ.get("HVOTES_CONTACT")
    ua = user_agent.replace("set HVOTES_CONTACT env var", contact) if contact else user_agent

    cfg = Config(
        start=_parse_date(start),
        end=end_date,
        keywords=_load_keywords(keywords, keywords_file),
        body=body,
        output_dir=output_dir,
        cache_dir=cache_dir,
        delay=delay,
        user_agent=ua,
        max_events=max_events,
        download=not no_download,
        skip_ocr=skip_ocr,
        auth_token=os.environ.get("HVOTES_TOKEN"),
    )
    logger = setup_logging(cfg.output_dir, verbose=verbose)
    logger.info("harlingen-votes starting: %s → %s, %d keywords",
                cfg.start, cfg.end, len(cfg.keywords))
    run(cfg)


votes_app = typer.Typer(add_completion=False,
                        help="Extract voting records from City Commission agenda packets.")
app.add_typer(votes_app, name="votes")


@votes_app.command("test")
def votes_test(
    file_id: int = typer.Option(..., help="Cached file_id of one packet to extract live."),
    meetings: Path = typer.Option(Path("output/meetings.json")),
    cache_dir: Path = typer.Option(Path("cache")),
) -> None:
    """One live extraction call (validates prompt + schema before batching)."""
    from .votes import collect_packets, run_test

    docs = [d for d in collect_packets(meetings, cache_dir) if d.file_id == file_id]
    if not docs:
        typer.echo(f"file_id {file_id} not found among City Commission packets", err=True)
        raise typer.Exit(1)
    result = run_test(docs[0])
    typer.echo(json.dumps(result, indent=2))


@votes_app.command("submit")
def votes_submit(
    meetings: Path = typer.Option(Path("output/meetings.json")),
    cache_dir: Path = typer.Option(Path("cache")),
    state: Path = typer.Option(Path("output/votes_batch.json")),
    dry_run: bool = typer.Option(False, "--dry-run", help="List docs; don't call the API."),
) -> None:
    """Submit the full extraction batch (Batch API, 50%% discount)."""
    from .votes import collect_packets, submit_batch

    docs = collect_packets(meetings, cache_dir)
    total_chars = sum(len(d.text) for d in docs)
    typer.echo(f"{len(docs)} documents, {total_chars:,} chars (~{total_chars // 4:,} tokens)")
    if dry_run:
        for d in docs:
            typer.echo(f"  {d.file_id}  {d.event_date}  {d.doc_type}  {d.n_pages}pp")
        return
    batch_id = submit_batch(docs, state)
    typer.echo(f"batch {batch_id} submitted; run `hvotes votes fetch` to poll")


@votes_app.command("fetch")
def votes_fetch(
    state: Path = typer.Option(Path("output/votes_batch.json")),
    raw: Path = typer.Option(Path("output/votes_raw.json")),
) -> None:
    """Poll the batch; when ended, write raw per-packet extractions."""
    from .votes import fetch_batch

    typer.echo(fetch_batch(state, raw))


@votes_app.command("build")
def votes_build(
    raw: Path = typer.Option(Path("output/votes_raw.json")),
    meetings: Path = typer.Option(Path("output/meetings.json")),
    votes: Path = typer.Option(Path("output/votes.json")),
    people: Path = typer.Option(Path("output/people.json")),
    people_map: Optional[Path] = typer.Option(
        None, help="Optional {variant: canonical} JSON to merge OCR name variants."),
) -> None:
    """Post-process raw extractions into votes.json + people.json."""
    from .votes import build_votes

    typer.echo(build_votes(raw, meetings, votes, people, people_map))


@app.command()
def bodies(
    delay: float = typer.Option(1.0, help="Seconds between requests."),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """List the meeting bodies/committees (use a name with --body)."""
    from .api import CivicClerkClient

    cfg = Config(start=DEFAULT_START, end=date.today(), keywords=[], delay=delay)
    cfg.ensure_dirs()
    setup_logging(cfg.output_dir, verbose=verbose)
    with CivicClerkClient(cfg) as client:
        for b in client.get_bodies():
            typer.echo(f"{b.id:>4}  {b.name}")


if __name__ == "__main__":
    app()
