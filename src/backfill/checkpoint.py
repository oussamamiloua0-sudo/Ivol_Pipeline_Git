"""JSON checkpoint helpers for resumable backfill runs."""
from __future__ import annotations

import json
from pathlib import Path


def load_progress(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_completed_date": None,
        "days_done":    0,
        "days_skipped": 0,
        "days_failed":  0,
        "failures":     {},
    }


def save_progress(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
