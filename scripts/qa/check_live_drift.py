#!/usr/bin/env python3
"""Live-checks the specific real examples this repo cites in its docs
against TMDB today, and reports what's drifted -- doesn't fail the build.

TMDB being an external, independently-changing service is the whole point
of several findings already documented in docs/RESULTS.md and
docs/VALIDATION_LOGIC.md (Bedrock/Comprehend/Textract/Rekognition drift,
the Omegle pair going from a real duplicate to a dead id). A CI check that
turns red every time TMDB changes something would just be noise -- this
prints a plain report instead, meant for a human to read (a scheduled
GitHub Actions job's step summary) and decide whether a doc needs
updating, not to gate a merge.

  TMDB_API_KEY=... python3 scripts/qa/check_live_drift.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.tmdb_client import tmdb_get


def check_movie_alive(session: requests.Session, api_key: str, movie_id: str) -> tuple[bool, str]:
    resp = tmdb_get(session, api_key, f"movie/{movie_id}")
    if resp.status_code == 200:
        return True, resp.json().get("title", "")
    return False, ""


def check_compilation_search(session: requests.Session, api_key: str, query: str, expected_id: str) -> dict:
    resp = tmdb_get(session, api_key, "search/movie", params={"query": query})
    results = resp.json().get("results", []) if resp.status_code == 200 else []
    ids = [str(r["id"]) for r in results]
    return {"n_results": len(results), "expected_id_present": expected_id in ids, "ids": ids}


def main() -> int:
    api_key = os.environ.get("TMDB_API_KEY", "").strip()
    if not api_key:
        print("TMDB_API_KEY not set -- skipping live drift check")
        return 0

    session = requests.Session()
    lines = ["# Live drift check\n"]

    lines.append("## Gate 9's Omegle example (docs/VALIDATION_LOGIC.md)\n")
    # As currently documented (2026-08-16): 1009049 already went dead
    # (phantom_duplicate_dead_id), 1743173 is still alive. If 1009049 ever
    # comes back, or 1743173 ever dies too, that's worth a doc update.
    for movie_id, expect_alive_when_documented in [("1009049", False), ("1743173", True)]:
        alive, title = check_movie_alive(session, api_key, movie_id)
        status = "alive" if alive else "404 / dead"
        note = "" if alive == expect_alive_when_documented else "  **DRIFTED from what's documented**"
        lines.append(f"- {movie_id} ({title or 'no title -- dead'}): {status}{note}")

    lines.append("\n## Gate 11's Sheets of Gore example (docs/VALIDATION_LOGIC.md)\n")
    result = check_compilation_search(session, api_key, "Sheets of Gore", "934611")
    lines.append(f"- search results: {result['n_results']}")
    lines.append(f"- id 934611 present: {result['expected_id_present']}")
    if not result["expected_id_present"] or result["n_results"] != 1:
        lines.append("  **DRIFTED from what's documented** (expected exactly 1 result, id 934611)")

    report = "\n".join(lines)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(report + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
