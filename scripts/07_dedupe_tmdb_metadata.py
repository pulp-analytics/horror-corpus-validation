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

Grouping key uses the full title, not a truncated prefix: an earlier version
of this comparison truncated to 60 chars, which silently merged unrelated
franchise entries whose titles only differ after that point (e.g. numbered
volumes of the same series) into false "duplicate" groups.

  TMDB_API_KEY=... python3 07_dedupe_tmdb_metadata.py --in data/sample_input/sample_100_ids.csv

Resumable: the per-id "is it still alive on TMDB" / "how many credits" checks
are cached in --cache (id -> alive/credits), appended to on each run, so an
interrupted run doesn't re-hit the TMDB API for ids it already checked. The
grouping/resolution step is cheap and local, so it's always redone in full
from whatever's in the cache.
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
from utils.resumable import load_done_ids, open_for_append, write_csv_rows
from utils.tmdb_client import tmdb_get

log = get_logger("dedupe_tmdb")


def norm(s: str) -> str:
    return (s or "").strip().lower()


def movie_is_alive(session: requests.Session, api_key: str, movie_id: str) -> bool:
    resp = tmdb_get(session, api_key, f"movie/{movie_id}")
    return resp.status_code == 200


def get_credits_count(session: requests.Session, api_key: str, movie_id: str) -> int:
    resp = tmdb_get(session, api_key, f"movie/{movie_id}/credits")
    if resp.status_code != 200:
        return 0
    d = resp.json()
    return len(d.get("cast", [])) + len(d.get("crew", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/duplicate_resolution.csv")
    ap.add_argument("--cache", default="data/sample_output/.tmdb_dedupe_cache.csv",
                     help="id->alive/credits cache, resumed across runs")
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

    candidates = [r for items in groups.values()
                  if len(items) >= 2 and len({r.get("poster_path", "").strip() for r in items if r.get("poster_path", "").strip()}) >= 2
                  for r in items]

    cache_path = Path(args.cache)
    cache_fields = ["id", "alive", "credits"]
    done = load_done_ids(cache_path)
    todo = [r for r in candidates if r["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already checked, {len(todo)} remaining")

    cf, cw = open_for_append(cache_path, cache_fields)
    try:
        for i, r in enumerate(todo, 1):
            alive = movie_is_alive(session, api_key, r["id"])
            time.sleep(0.1)
            credits_count = get_credits_count(session, api_key, r["id"]) if alive else 0
            if alive:
                time.sleep(0.1)
            cw.writerow({"id": r["id"], "alive": int(alive), "credits": credits_count})
            if i % 25 == 0:
                log.info(f"{i}/{len(todo)}")
    finally:
        cf.close()

    with cache_path.open(newline="", encoding="utf-8") as f:
        cache = {r["id"]: r for r in csv.DictReader(f)}

    out_rows = []
    n_checked = 0
    for key, items in groups.items():
        posters = {r.get("poster_path", "").strip() for r in items if r.get("poster_path", "").strip()}
        if len(items) < 2 or len(posters) < 2:
            continue

        n_checked += len(items)
        live = [r for r in items if cache.get(r["id"], {}).get("alive") == "1"]

        if len(live) < 2:
            resolution = "phantom_duplicate_dead_id" if len(items) > len(live) else "no_duplicate"
            keep = live[0]["id"] if live else ""
        else:
            best = max(live, key=lambda r: int(cache.get(r["id"], {}).get("credits") or 0))
            resolution = "duplicate_resolved_by_credits_count"
            keep = best["id"]

        for r in items:
            out_rows.append({"group_title": key[0], "group_year": key[1], "id": r["id"],
                              "keep": int(r["id"] == keep), "resolution": resolution})

    out_path = Path(args.out)
    write_csv_rows(out_path, out_rows)

    log.info(f"checked {n_checked} ids across {len(out_rows) and len({r['group_title'] for r in out_rows})} candidate groups")
    log.info(f"wrote {out_path} ({len(out_rows)} rows)")


if __name__ == "__main__":
    main()
