"""Bucket classification, Greece-eligibility gate, and stack tagging.

All the tunable knobs live in ``config.json`` at the project root, so you
can adjust keywords and location rules without touching code.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def classify_bucket(title: str, cfg: dict) -> str | None:
    """Return 'em', 'ai', or None based on the job title."""
    t = (title or "").lower()
    for term in cfg["buckets"]["em"]:
        if term in t:
            return "em"
    for term in cfg["buckets"]["ai"]:
        if term in t:
            return "ai"
    return None


def location_status(location: str, cfg: dict) -> str:
    """Classify a posting's location against the Greece gate.

    Returns:
        'eligible' - clearly open to Greece (worldwide/Europe/EMEA/Greece/...)
        'excluded' - clearly locked to a region that excludes Greece
        'verify'   - unknown / ambiguous (e.g. bare "Remote") - worth a look
    """
    loc = (location or "").lower().strip()
    if not loc:
        return "verify"
    for tok in cfg["location_eligible_tokens"]:
        if tok in loc:
            return "eligible"
    for tok in cfg["location_excluded_tokens"]:
        if tok in loc:
            return "excluded"
    return "verify"


def stack_tags(text: str, cfg: dict) -> list[str]:
    """Label a posting with any stack hints it matches (rails/.net/go/ai...)."""
    t = (text or "").lower()
    found = []
    for label, hints in cfg.get("stack_hints", {}).items():
        if any(h in t for h in hints):
            found.append(label)
    return found
