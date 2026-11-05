"""Persistence for the set of already-seen job ids.

Used so `new_jobs` only ever reports a posting once. Stored as a plain
JSON list next to the package in ``data/seen.json``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEEN_FILE = DATA_DIR / "seen.json"


def job_id(job: dict) -> str:
    """Stable short id for a posting, from company + title + url."""
    key = "|".join(
        [
            str(job.get("company", "")).strip().lower(),
            str(job.get("title", "")).strip().lower(),
            str(job.get("url", "")).strip().lower(),
        ]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def load_seen() -> set[str]:
    try:
        return set(json.loads(SEEN_FILE.read_text("utf-8")))
    except Exception:
        return set()


def save_seen(ids: set[str]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SEEN_FILE.write_text(json.dumps(sorted(ids)), "utf-8")
    except Exception:
        # Persistence is best-effort; never crash a run over it.
        pass
