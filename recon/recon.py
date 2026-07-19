"""
Phase 1 reconnaissance: discover the CivicClerk backend API.

Loads the Harlingen CivicClerk portal in headless Chromium, drives the SPA to
load its meetings list, and records every XHR/fetch request (URL, method, query
params, response status, content-type, and a sample of each JSON body) to
recon/network_log.json.

We do NOT parse rendered HTML. The goal is only to learn the API shape.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://harlingentx.portal.civicclerk.com/"
OUT_PATH = Path(__file__).parent / "network_log.json"
BODY_SAMPLE_CHARS = 4000  # cap stored response bodies so the log stays readable

# Only bother capturing bodies for things that look like data, not assets.
INTERESTING = ("xhr", "fetch")


def main() -> None:
    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "hvotes-recon/0.1 (+civic-records research; contact via repo) "
                "Mozilla/5.0"
            )
        )
        page = context.new_page()

        def on_response(response) -> None:
            req = response.request
            if req.resource_type not in INTERESTING:
                return
            url = response.url
            split = urlsplit(url)
            entry: dict = {
                "method": req.method,
                "url": url,
                "host": split.netloc,
                "path": split.path,
                "query": {k: v for k, v in parse_qs(split.query).items()},
                "resource_type": req.resource_type,
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
            }
            # Capture a JSON body sample where possible.
            ctype = entry["content_type"].lower()
            if "json" in ctype or split.path.endswith(".json"):
                try:
                    body = response.text()
                    entry["body_sample"] = body[:BODY_SAMPLE_CHARS]
                    entry["body_truncated"] = len(body) > BODY_SAMPLE_CHARS
                except Exception as exc:  # body may be unavailable
                    entry["body_error"] = repr(exc)
            captured.append(entry)
            print(f"[{response.status}] {req.method} {url}")

        page.on("response", on_response)

        print(f"Loading {PORTAL_URL} ...")
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)

        # Let the SPA boot and fire its initial data calls.
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass

        # Nudge the app: scroll and give late XHRs time to land.
        for _ in range(3):
            page.mouse.wheel(0, 2000)
            time.sleep(2)

        # Try to surface the meetings/agendas list explicitly if a nav link exists.
        for label in ("Meetings", "Agendas", "Events", "Calendar"):
            try:
                link = page.get_by_text(label, exact=False).first
                if link.is_visible():
                    link.click(timeout=3000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    time.sleep(2)
                    break
            except Exception:
                continue

        time.sleep(3)
        browser.close()

    OUT_PATH.write_text(json.dumps(captured, indent=2))
    print(f"\nCaptured {len(captured)} XHR/fetch requests -> {OUT_PATH}")

    # Quick summary of distinct hosts seen.
    hosts: dict[str, int] = {}
    for e in captured:
        hosts[e["host"]] = hosts.get(e["host"], 0) + 1
    print("\nHosts seen:")
    for host, n in sorted(hosts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3d}  {host}")


if __name__ == "__main__":
    main()
