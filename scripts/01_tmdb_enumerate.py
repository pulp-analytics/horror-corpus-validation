#!/usr/bin/env python3
"""Enumerate TMDB movies by genre (default: Horror, genre=27), non-adult,
past Discover's 10k/page-500 cap.

TMDB's /discover/movie endpoint caps at 500 pages (10,000 results) per query.
A single genre query returns far more than that for a broad genre, so this
shards by year; a year shard still over 10k would need splitting further
(e.g. by vote_count band) -- not needed for any single TMDB genre as of
this writing, but worth knowing if you point this at a very broad genre.

Not locked to horror: --genre accepts any TMDB genre id. This project also
ran the same methodology against Sci-Fi (878), Mystery (9648), and
Thriller (53) as adjacent genres -- see the full genre id list at
https://developer.themoviedb.org/reference/genre-movie-list

  TMDB_API_KEY=... python3 01_tmdb_enumerate.py --out data/sample_output/tmdb_horror_ids.csv
  python3 01_tmdb_enumerate.py --genre 878 --out data/sample_output/tmdb_scifi_ids.csv
  python3 01_tmdb_enumerate.py --limit 100 --out data/sample_output/sample_100_ids.csv

Resumable: years already fully enumerated are tracked in a sibling
--out.years_done file, and ids already written to --out are skipped, so an
interrupted multi-year enumeration resumes rather than restarting from
scratch -- this matters more once --genre lets a single run cover far more
years/results than the horror-only default.
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
from utils.resumable import open_for_append

log = get_logger("tmdb_enumerate")
DISCOVER = "https://api.themoviedb.org/3/discover/movie"
FIELDS = ["id", "title", "original_title", "release_date", "poster_path",
          "original_language", "popularity", "vote_count", "vote_average", "overview"]


def fetch_page(session: requests.Session, api_key: str, genre_id: int, year: int, page: int) -> dict:
    params = {
        "api_key": api_key, "with_genres": genre_id, "include_adult": "false",
        "primary_release_year": year, "page": page, "sort_by": "popularity.desc",
    }
    resp = session.get(DISCOVER, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def enumerate_year(session: requests.Session, api_key: str, genre_id: int, year: int) -> list[dict]:
    first = fetch_page(session, api_key, genre_id, year, 1)
    total_pages = min(first.get("total_pages", 1), 500)
    rows = list(first.get("results", []))
    for page in range(2, total_pages + 1):
        data = fetch_page(session, api_key, genre_id, year, page)
        rows.extend(data.get("results", []))
        time.sleep(0.05)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--genre", type=int, default=TMDB_HORROR_GENRE_ID,
                     help="TMDB genre id (default: 27, Horror). Sci-Fi=878, Mystery=9648, Thriller=53, ...")
    ap.add_argument("--start-year", type=int, default=2020)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--limit", type=int, default=0, help="stop after N rows (for a quick sample)")
    ap.add_argument("--out", default="data/sample_output/tmdb_horror_ids.csv")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()
    out_path = Path(args.out)
    years_done_path = out_path.with_suffix(out_path.suffix + ".years_done")

    seen_ids: set[int] = set()
    if out_path.exists():
        with out_path.open(newline="", encoding="utf-8") as f:
            seen_ids = {int(r["id"]) for r in csv.DictReader(f) if r.get("id")}
    years_done: set[int] = set()
    if years_done_path.exists():
        years_done = {int(y) for y in years_done_path.read_text().split()}
    if seen_ids or years_done:
        log.info(f"resuming: {len(seen_ids):,} ids and {len(years_done)} year(s) already done")

    f, w = open_for_append(out_path, FIELDS)
    try:
        for year in range(args.end_year, args.start_year - 1, -1):
            if args.limit and len(seen_ids) >= args.limit:
                break
            if year in years_done:
                continue
            log.info(f"year {year} (genre={args.genre})...")
            limit_hit = False
            for m in enumerate_year(session, api_key, args.genre, year):
                if args.limit and len(seen_ids) >= args.limit:
                    limit_hit = True
                    break
                if m["id"] not in seen_ids:
                    seen_ids.add(m["id"])
                    w.writerow({k: m.get(k, "") for k in FIELDS})
            f.flush()
            if not limit_hit:
                years_done.add(year)
                years_done_path.write_text(" ".join(str(y) for y in sorted(years_done)))
            log.info(f"  running total: {len(seen_ids):,}")
    finally:
        f.close()

    log.info(f"wrote {out_path} ({len(seen_ids):,} rows)")


if __name__ == "__main__":
    main()
