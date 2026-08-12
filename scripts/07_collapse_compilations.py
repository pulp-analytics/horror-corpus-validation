#!/usr/bin/env python3
"""Find posters shared across multiple catalog ids (a strong signal that
they're really segments of one VHS/DVD compilation or TV anthology, not
individually-posterized films), and check whether the compilation itself
has its own TMDB entry to collapse into.

Real example: 6 short films by the same director were each listed
separately in TMDB, but every one of them had the exact same "Sheets of
Gore" compilation cover as its poster. TMDB also had a *separate*, correct
entry for "Sheets of Gore" itself (with its own poster and metadata) — the
fix was collapsing the 6 segment ids into that one, not picking one of the
6 arbitrarily. Not every case has a rescuable id like that: when TMDB has
no entry for the compilation/anthology itself, this script reports the
group but does not auto-resolve it — that's a judgment call (see
docs/RESULTS.md, "Bite Size Halloween" / "Late Night Horror").

  TMDB_API_KEY=... python3 07_collapse_compilations.py --in data/sample_output/vision_title_check.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_tmdb_key
from utils.logging_setup import get_logger
from utils.text_match import title_overlap_score

log = get_logger("collapse_compilations")


def search_movie(session: requests.Session, api_key: str, query: str) -> list[dict]:
    if not query.strip():
        return []
    resp = session.get("https://api.themoviedb.org/3/search/movie", params={"api_key": api_key, "query": query}, timeout=15)
    if resp.status_code != 200:
        return []
    return resp.json().get("results", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_output/vision_title_check.csv")
    ap.add_argument("--poster-col", default="poster_path")
    ap.add_argument("--text-col", default="text_you_read")
    ap.add_argument("--out", default="data/sample_output/compilation_groups.csv")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_poster: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        p = r.get(args.poster_col, "").strip()
        if p:
            by_poster[p].append(r)

    shared = {p: items for p, items in by_poster.items() if len(items) > 1}
    log.info(f"posters shared by 2+ catalog ids: {len(shared)} groups, {sum(len(v) for v in shared.values())} ids")

    out_rows = []
    for poster, items in shared.items():
        query_text = items[0].get(args.text_col, "")
        candidates = search_movie(session, api_key, query_text)
        time.sleep(0.1)

        canonical_id, canonical_title, resolution = "", "", "no_compilation_entry_found"
        if len(candidates) == 1:
            c = candidates[0]
            if title_overlap_score(query_text, c.get("title", "")) > 0:
                canonical_id, canonical_title = str(c["id"]), c.get("title", "")
                resolution = "compilation_entry_found"

        for r in items:
            out_rows.append({
                "poster_path": poster, "segment_id": r["id"], "segment_title": r.get("title", ""),
                "shared_text": query_text, "canonical_id": canonical_id,
                "canonical_title": canonical_title, "resolution": resolution,
            })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_rows:
        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)

    n_resolved = len({r["poster_path"] for r in out_rows if r["resolution"] == "compilation_entry_found"})
    log.info(f"wrote {out_path} — {n_resolved}/{len(shared)} groups had a rescuable compilation id in TMDB")
    log.info("groups with no compilation entry need a human call: exclude, or leave unresolved (see docs/RESULTS.md)")


if __name__ == "__main__":
    main()
