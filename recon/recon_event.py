"""
Phase 1 recon, part 2: discover the per-event DOCUMENT / file-download endpoints.

The first pass captured bodies (EventCategories) and the Events list, but not the
published-files endpoint, because it never opened a single meeting. This pass
clicks into an event and logs the resulting XHR/fetch traffic, then prints the
file-related responses and the final SPA route so we learn the URL pattern.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://harlingentx.portal.civicclerk.com/"
OUT_PATH = Path(__file__).parent / "network_log_event.json"
BODY_SAMPLE_CHARS = 6000
INTERESTING = ("xhr", "fetch")


def main() -> None:
    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=("hvotes-recon/0.1 (+civic-records research) Mozilla/5.0")
        )
        page = context.new_page()

        def on_response(response) -> None:
            req = response.request
            if req.resource_type not in INTERESTING:
                return
            split = urlsplit(response.url)
            entry: dict = {
                "method": req.method,
                "url": response.url,
                "host": split.netloc,
                "path": split.path,
                "query": {k: v for k, v in parse_qs(split.query).items()},
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
            }
            ctype = entry["content_type"].lower()
            if "json" in ctype:
                try:
                    body = response.text()
                    entry["body_sample"] = body[:BODY_SAMPLE_CHARS]
                    entry["body_truncated"] = len(body) > BODY_SAMPLE_CHARS
                except Exception as exc:
                    entry["body_error"] = repr(exc)
            captured.append(entry)

        page.on("response", on_response)

        print(f"Loading {PORTAL_URL} ...")
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
        time.sleep(3)

        # Click the first meeting/event entry. CivicClerk renders event rows as
        # clickable list items; try a few selector strategies.
        clicked = False
        candidates = [
            "li[role='button']",
            "[class*='event'] [role='button']",
            "[class*='MuiListItem']",
            "a[href*='event']",
        ]
        for sel in candidates:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=4000)
                    clicked = True
                    print(f"Clicked using selector: {sel}")
                    break
            except Exception:
                continue

        if not clicked:
            # Fallback: click the first event name text we saw in the API.
            try:
                page.get_by_text("Regular Meeting", exact=False).first.click(timeout=4000)
                clicked = True
                print("Clicked first 'Regular Meeting' text")
            except Exception:
                print("WARNING: could not click into an event")

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        time.sleep(4)

        print("Final SPA URL:", page.url)
        browser.close()

    OUT_PATH.write_text(json.dumps(captured, indent=2))
    print(f"\nCaptured {len(captured)} XHR/fetch requests -> {OUT_PATH}")
    print("\nAPI calls (civicclerk):")
    for e in captured:
        if "api.civicclerk.com" in e["host"]:
            print(f"  [{e['status']}] {e['method']} {e['path']}"
                  + (f"?{e['query']}" if e["query"] else ""))


if __name__ == "__main__":
    main()
