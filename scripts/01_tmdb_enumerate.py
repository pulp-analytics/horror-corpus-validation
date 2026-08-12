#!/usr/bin/env python3
"""Enumerate TMDB horror (genre=27, non-adult) past Discover's 10k/page-500 cap.

TMDB's /discover/movie endpoint caps at 500 pages (10,000 results) per query.
A single genre=27 query returns far more than that, so this shards by year;
any year shard still over 10k gets split further by vote_count band.

  TMDB_API_KEY=... python3 01_tmdb_enumerate.py --out data/sample_output/tmdb_horror_ids.csv
  python3 01_tmdb_enumerate.py --limit 100 --out data/sample_output/sample_100_ids.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_tmdb_key
from utils.constants import TMDB_HORROR_GENRE_ID
from utils.logging_setup import get_logger

log = get_logger("tmdb_enumerate")
DISCOVER = "https://api.themoviedb.org/3/discover/movie"
FIELDS = ["id", "title", "original_title", "release_date", "poster_path",
          "original_language", "popularity", "vote_count", "vote_average", "overview"]


def fetch_page(session: requests.Session, api_key: str, year: int, page: int) -> dict:
    params = {
        "api_key": api_key, "with_genres": TMDB_HORROR_GENRE_ID, "include_adult": "false",
        "primary_release_year": year, "page": page, "sort_by": "popularity.desc",
    }
    resp = session.get(DISCOVER, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def enumerate_year(session: requests.Session, api_key: str, year: int) -> list[dict]:
    first = fetch_page(session, api_key, year, 1)
    total_pages = min(first.get("total_pages", 1), 500)
    rows = list(first.get("results", []))
    for page in range(2, total_pages + 1):
        data = fetch_page(session, api_key, year, page)
        rows.extend(data.get("results", []))
        time.sleep(0.05)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2020)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--limit", type=int, default=0, help="stop after N rows (for a quick sample)")
    ap.add_argument("--out", default="data/sample_output/tmdb_horror_ids.csv")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()
    seen: dict[int, dict] = {}

    for year in range(args.end_year, args.start_year - 1, -1):
        log.info(f"year {year}...")
        for m in enumerate_year(session, api_key, year):
            seen[m["id"]] = m
        log.info(f"  running total: {len(seen):,}")
        if args.limit and len(seen) >= args.limit:
            break

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for m in list(seen.values())[: args.limit or None]:
            w.writerow({k: m.get(k, "") for k in FIELDS})

    log.info(f"wrote {out_path} ({min(len(seen), args.limit or len(seen)):,} rows)")


if __name__ == "__main__":
    main()
