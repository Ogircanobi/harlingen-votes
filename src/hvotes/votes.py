"""Vote extraction from City Commission agenda packets (option B: LLM extraction).

Harlingen publishes no standalone minutes; approved minutes of meeting X are
embedded inside a LATER meeting's agenda packet. This module sends each cached
packet's text to the Claude API (Batch API, structured outputs) and extracts
per-meeting attendance + motions/votes, with verbatim quotes for provenance.

Flow:
    hvotes votes test --file-id 1248     # one live call, validate prompt+schema
    hvotes votes submit                  # create the batch (all packets)
    hvotes votes fetch                   # poll; when ended, write votes_raw.json
    hvotes votes build                   # votes_raw.json -> votes.json + people.json
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

MODEL = "claude-opus-4-8"
MAX_TOKENS = 32000
DEFAULT_DOC_TYPES = ("Agenda Packet", "Other")
DEFAULT_BODY = "City Commission"

SYSTEM_PROMPT = """\
You extract voting records from City of Harlingen, TX public meeting documents.

You are given the OCR'd/extracted text of one agenda packet, with [[PAGE N]]
markers. Agenda packets often embed the APPROVED MINUTES of one or more EARLIER
meetings (usually as the attachment behind an "Approval of Minutes" item). Your
job is to find every minutes section and extract what actually happened.

Rules:
- Only extract from minutes narrative (past tense, records what occurred).
  NEVER treat the packet's own agenda listing (future business, "consideration
  and possible action ...") as a vote record.
- For each meeting whose minutes appear: the meeting date, attendance of the
  MAYOR AND COMMISSIONERS ONLY (present and absent; exclude staff, attorneys,
  and members of the public), and every agenda item that records a motion.
- For each motion: who moved, who seconded, the outcome, whether it carried
  unanimously, and individual ayes/nays/abstentions ONLY when the minutes name
  them individually. Do not invent individual votes for unanimous outcomes.
- verbatim_quote: copy the exact sentence(s) from the text that record the
  motion and its result, preserving OCR errors as written.
- The text is OCR'd and noisy ("Pro Tern" means "Pro Tem", "de Ia Paz" means
  "de la Paz", etc.). In name fields, give your best-effort corrected spelling;
  the verbatim_quote keeps the raw form.
- Be conservative: use null / empty arrays for anything the text does not
  state. Set confidence to "low" when the OCR is too garbled to be sure.
- If the packet contains no minutes at all, return minutes_found=false with an
  empty meetings array."""

_PERSON = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "role": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["name", "role"],
}

_MOTION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "moved_by": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "seconded_by": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "outcome": {
            "type": "string",
            "enum": ["carried", "failed", "tabled", "withdrawn",
                     "died_for_lack_of_second", "no_vote_recorded", "other"],
        },
        "unanimous": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        "ayes": {"type": "array", "items": {"type": "string"}},
        "nays": {"type": "array", "items": {"type": "string"}},
        "abstentions": {"type": "array", "items": {"type": "string"}},
        "tally": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "verbatim_quote": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["moved_by", "seconded_by", "outcome", "unanimous", "ayes",
                 "nays", "abstentions", "tally", "verbatim_quote", "confidence"],
}

_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "item_label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "description": {"type": "string"},
        "page": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "motion": {"anyOf": [_MOTION, {"type": "null"}]},
    },
    "required": ["item_label", "description", "page", "motion"],
}

_MEETING = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "meeting_date": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "meeting_description": {"type": "string"},
        "page_start": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "present": {"type": "array", "items": _PERSON},
        "absent": {"type": "array", "items": _PERSON},
        "items": {"type": "array", "items": _ITEM},
    },
    "required": ["meeting_date", "meeting_description", "page_start",
                 "present", "absent", "items"],
}

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "minutes_found": {"type": "boolean"},
        "meetings": {"type": "array", "items": _MEETING},
    },
    "required": ["minutes_found", "meetings"],
}


@dataclass
class PacketDoc:
    file_id: int
    doc_type: str
    event_id: int
    event_date: str
    portal_url: str
    source_url: str
    text: str
    n_pages: int


def _load_cached_text(cache_dir: Path, file_id: int) -> tuple[str, int] | None:
    fp = cache_dir / "text" / f"{file_id}.json"
    if not fp.exists():
        return None
    d = json.loads(fp.read_text())
    pages = d.get("pages") or []
    parts = [f"[[PAGE {p.get('page', i + 1)}]]\n{p.get('text') or ''}"
             for i, p in enumerate(pages)]
    text = "\n\n".join(parts).strip()
    if not text:
        return None
    return text, len(pages)


def collect_packets(
    meetings_path: Path,
    cache_dir: Path,
    body: str = DEFAULT_BODY,
    doc_types: tuple[str, ...] = DEFAULT_DOC_TYPES,
) -> list[PacketDoc]:
    events = json.loads(meetings_path.read_text())
    docs: list[PacketDoc] = []
    for ev in events:
        if ev.get("body") != body:
            continue
        for f in ev.get("files") or []:
            if f.get("type") not in doc_types:
                continue
            loaded = _load_cached_text(cache_dir, int(f["file_id"]))
            if loaded is None:
                continue
            text, n_pages = loaded
            docs.append(PacketDoc(
                file_id=int(f["file_id"]),
                doc_type=str(f["type"]),
                event_id=int(ev["event_id"]),
                event_date=str(ev["date"]),
                portal_url=str(ev.get("portal_url") or ""),
                source_url=str(f.get("source_url") or ""),
                text=text,
                n_pages=n_pages,
            ))
    return docs


def _user_message(doc: PacketDoc) -> str:
    return (
        f"Agenda packet for the City Commission meeting of {doc.event_date} "
        f"(document type: {doc.doc_type}, {doc.n_pages} pages). Extract any "
        f"embedded minutes and their vote records.\n\n{doc.text}"
    )


def _request_params(doc: PacketDoc) -> dict[str, Any]:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        "messages": [{"role": "user", "content": _user_message(doc)}],
    }


# ---------------------------------------------------------------- live test

def run_test(doc: PacketDoc) -> dict[str, Any]:
    """One live (non-batch) extraction; returns the parsed JSON."""
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(**_request_params(doc)) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("API refused the request (stop_reason=refusal)")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("Output truncated at max_tokens; raise MAX_TOKENS")
    text = next(b.text for b in message.content if b.type == "text")
    result = json.loads(text)
    result["_usage"] = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }
    return result


# ------------------------------------------------------------------- batch

def submit_batch(docs: list[PacketDoc], state_path: Path) -> str:
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    requests = [
        Request(custom_id=f"packet-{doc.file_id}",
                params=MessageCreateParamsNonStreaming(**_request_params(doc)))
        for doc in docs
    ]
    batch = client.messages.batches.create(requests=requests)
    state = {
        "batch_id": batch.id,
        "model": MODEL,
        "docs": {f"packet-{d.file_id}": {
            "file_id": d.file_id, "doc_type": d.doc_type,
            "event_id": d.event_id, "event_date": d.event_date,
            "portal_url": d.portal_url, "source_url": d.source_url,
        } for d in docs},
    }
    state_path.write_text(json.dumps(state, indent=2))
    return batch.id


def fetch_batch(state_path: Path, raw_path: Path) -> str:
    """Check batch status; when ended, write raw results. Returns status."""
    import anthropic

    state = json.loads(state_path.read_text())
    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(state["batch_id"])
    if batch.processing_status != "ended":
        counts = batch.request_counts
        return (f"{batch.processing_status} "
                f"(processing={counts.processing} succeeded={counts.succeeded} "
                f"errored={counts.errored})")

    results: dict[str, Any] = {}
    for result in client.messages.batches.results(state["batch_id"]):
        entry: dict[str, Any] = {"result_type": result.result.type}
        if result.result.type == "succeeded":
            msg = result.result.message
            if msg.stop_reason in ("refusal", "max_tokens"):
                entry["result_type"] = f"bad_stop:{msg.stop_reason}"
            else:
                text = next((b.text for b in msg.content if b.type == "text"), "")
                try:
                    entry["extraction"] = json.loads(text)
                except json.JSONDecodeError:
                    entry["result_type"] = "bad_json"
                    entry["raw_text"] = text[:2000]
        elif result.result.type == "errored":
            entry["error"] = str(result.result.error)
        results[result.custom_id] = entry

    raw_path.write_text(json.dumps(
        {"batch_id": state["batch_id"], "docs": state["docs"], "results": results},
        indent=2))
    ok = sum(1 for r in results.values() if r["result_type"] == "succeeded")
    return f"ended: {ok}/{len(results)} succeeded -> {raw_path}"


# ------------------------------------------------------------------- build

_TITLE_RE = re.compile(
    r"^(mayor\s+pro[\s-]?te[mr]n?\.?|mayor|vice[\s-]?mayor|commissioner|comm\.?|"
    r"dr\.?|mr\.?|mrs\.?|ms\.?)\s+", re.IGNORECASE)


def _norm_name(name: str) -> str:
    s = re.sub(r"\s+", " ", name or "").strip().strip(".,;:")
    while True:
        stripped = _TITLE_RE.sub("", s)
        if stripped == s:
            break
        s = stripped.strip()
    return s


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def build_votes(raw_path: Path, meetings_path: Path, votes_path: Path,
                people_path: Path, people_map_path: Optional[Path] = None) -> str:
    raw = json.loads(raw_path.read_text())
    events = json.loads(meetings_path.read_text())
    by_date = {(ev["body"], ev["date"]): ev for ev in events}

    people_map: dict[str, str] = {}
    if people_map_path and people_map_path.exists():
        people_map = json.loads(people_map_path.read_text())

    def canon(name: str | None) -> str | None:
        if not name:
            return None
        n = _norm_name(name)
        return people_map.get(n, n)

    # squashed source text per file, for verbatim-quote verification
    cache_dir = raw_path.parent.parent / "cache"

    def squashed_source(file_id: int) -> str:
        loaded = _load_cached_text(cache_dir, file_id)
        return _squash(loaded[0]) if loaded else ""

    votes: list[dict[str, Any]] = []
    name_counts: dict[str, int] = {}
    source_cache: dict[int, str] = {}

    for custom_id, entry in raw["results"].items():
        if entry.get("result_type") != "succeeded":
            continue
        meta = raw["docs"][custom_id]
        extraction = entry["extraction"]
        fid = meta["file_id"]
        if fid not in source_cache:
            source_cache[fid] = squashed_source(fid)

        for mtg in extraction.get("meetings", []):
            present = [canon(p["name"]) for p in mtg.get("present", []) if p.get("name")]
            absent = [canon(p["name"]) for p in mtg.get("absent", []) if p.get("name")]
            for nm in present + absent:
                name_counts[nm] = name_counts.get(nm, 0) + 1
            origin = by_date.get((DEFAULT_BODY, mtg.get("meeting_date")))

            for item in mtg.get("items", []):
                motion = item.get("motion")
                if not motion:
                    continue
                ayes = [canon(n) for n in motion.get("ayes", [])]
                derived_ayes = False
                if (not ayes and motion.get("outcome") == "carried"
                        and motion.get("unanimous") and present):
                    ayes = list(present)
                    derived_ayes = True
                quote = motion.get("verbatim_quote") or ""
                quote_verified = bool(quote) and _squash(quote) in source_cache[fid]
                for nm in filter(None, [canon(motion.get("moved_by")),
                                        canon(motion.get("seconded_by"))]):
                    name_counts[nm] = name_counts.get(nm, 0) + 1
                votes.append({
                    "meeting_date": mtg.get("meeting_date"),
                    "meeting_description": mtg.get("meeting_description"),
                    "item_label": item.get("item_label"),
                    "description": item.get("description"),
                    "moved_by": canon(motion.get("moved_by")),
                    "seconded_by": canon(motion.get("seconded_by")),
                    "outcome": motion.get("outcome"),
                    "unanimous": motion.get("unanimous"),
                    "ayes": ayes,
                    "derived_ayes": derived_ayes,
                    "nays": [canon(n) for n in motion.get("nays", [])],
                    "abstentions": [canon(n) for n in motion.get("abstentions", [])],
                    "present": present,
                    "absent": absent,
                    "tally": motion.get("tally"),
                    "verbatim_quote": quote,
                    "quote_verified": quote_verified,
                    "confidence": motion.get("confidence"),
                    "page": item.get("page"),
                    "source": {
                        "packet_file_id": fid,
                        "packet_event_id": meta["event_id"],
                        "packet_event_date": meta["event_date"],
                        "source_url": meta["source_url"],
                        "portal_url": meta["portal_url"],
                    },
                    "origin_event": {
                        "event_id": origin["event_id"],
                        "portal_url": origin.get("portal_url"),
                    } if origin else None,
                })

    votes.sort(key=lambda v: (v["meeting_date"] or "", v["item_label"] or ""))
    votes_path.write_text(json.dumps(votes, indent=2))
    people_path.write_text(json.dumps(
        dict(sorted(name_counts.items(), key=lambda kv: -kv[1])), indent=2))
    verified = sum(1 for v in votes if v["quote_verified"])
    return (f"{len(votes)} motions across "
            f"{len({v['meeting_date'] for v in votes})} meetings -> {votes_path}; "
            f"{verified}/{len(votes)} quotes verified; "
            f"{len(name_counts)} name variants -> {people_path}")
