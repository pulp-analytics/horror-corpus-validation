#!/usr/bin/env python3
"""Find TMDB duplicate-entry pairs: same title+year+overview, different id,
different poster_path.

This is metadata-based dedup, not visual/perceptual hashing — TMDB
occasionally has the exact same film listed twice under two ids (we found
this happens for ~0.1% of a 145k-title corpus), usually with one id carrying
a cropped/wrong poster and the other the correct one. A live check against
the TMDB API is required before trusting a "duplicate": in our real run, 38
of 72 candidate groups turned out to be one-sided — one of the two ids no
longer existed in TMDB at all (404), so there was no real duplicate, just a
stale reference. See docs/RESULTS.md.

  TMDB_API_KEY=... python3 07_dedupe_tmdb_metadata.py --in data/sample_input/sample_100_ids.csv
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

log = get_logger("dedupe_tmdb")


def norm(s: str) -> str:
    return (s or "").strip().lower()[:60]


def movie_is_alive(session: requests.Session, api_key: str, movie_id: str) -> bool:
    resp = session.get(f"https://api.themoviedb.org/3/movie/{movie_id}", params={"api_key": api_key}, timeout=15)
    return resp.status_code == 200


def get_credits_count(session: requests.Session, api_key: str, movie_id: str) -> int:
    resp = session.get(f"https://api.themoviedb.org/3/movie/{movie_id}/credits", params={"api_key": api_key}, timeout=15)
    if resp.status_code != 200:
        return 0
    d = resp.json()
    return len(d.get("cast", [])) + len(d.get("crew", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/duplicate_resolution.csv")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key = (norm(row.get("title", "")), (row.get("release_date", "") or "")[:4], norm(row.get("overview", "")))
        if all(key):
            groups[key].append(row)

    out_rows = []
    n_checked = 0
    for key, items in groups.items():
        posters = {r.get("poster_path", "").strip() for r in items if r.get("poster_path", "").strip()}
        if len(items) < 2 or len(posters) < 2:
            continue

        live = []
        for r in items:
            n_checked += 1
            alive = movie_is_alive(session, api_key, r["id"])
            time.sleep(0.1)
            if alive:
                live.append(r)

        if len(live) < 2:
            resolution = "phantom_duplicate_dead_id" if len(items) > len(live) else "no_duplicate"
            keep = live[0]["id"] if live else ""
        else:
            scored = [(r, get_credits_count(session, api_key, r["id"])) for r in live]
            time.sleep(0.1 * len(live))
            best = max(scored, key=lambda x: x[1])
            resolution = "duplicate_resolved_by_credits_count"
            keep = best[0]["id"]

        for r in items:
            out_rows.append({"group_title": key[0], "group_year": key[1], "id": r["id"],
                              "keep": int(r["id"] == keep), "resolution": resolution})

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_rows:
        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)

    log.info(f"checked {n_checked} ids across {len(out_rows) and len({r['group_title'] for r in out_rows})} candidate groups")
    log.info(f"wrote {out_path} ({len(out_rows)} rows)")


if __name__ == "__main__":
    main()
