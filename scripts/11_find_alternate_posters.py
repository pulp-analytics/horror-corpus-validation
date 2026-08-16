#!/usr/bin/env python3
"""Discover and download alternate poster candidates from TMDB, for films
flagged as a possible title mismatch -- gate 8-9's first half. Ports the
real project's multi_poster_pipeline.py discover+download commands (see
that script's docstring), collapsed into one step since this repo doesn't
need the separate embed/cluster stage (that's about deduplicating near-
identical variants across the *whole* corpus, a different concern from
"does one flagged film have a better alternate poster").

One TMDB call per id (`append_to_response=images`) gets the full poster
list, ranked by vote_average/vote_count/height (the real project's own
ranking), capped at --max-per-id. No AWS involved.

  export TMDB_API_KEY=...
  python3 11_find_alternate_posters.py --in data/sample_output/vision_title_check.csv

Resumable: skips ids whose variant directory already has files.
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
from utils.logging_setup import get_logger
from utils.resumable import shard_rows, write_csv_rows

log = get_logger("find_alternate_posters")
TMDB_IMG = "https://image.tmdb.org/t/p/w500"


def discover_images(session: requests.Session, api_key: str, movie_id: str, langs: str) -> dict:
    resp = session.get(
        f"https://api.themoviedb.org/3/movie/{movie_id}",
        params={"api_key": api_key, "append_to_response": "images", "include_image_language": langs},
        timeout=30,
    )
    if resp.status_code != 200:
        return {}
    return resp.json()


def rank_posters(data: dict) -> list[dict]:
    """Real project's own ranking: vote_average, then vote_count, then
    height, all descending -- ensures the current primary is present even
    if the language filter would have dropped it."""
    primary = data.get("poster_path") or ""
    posters = list((data.get("images") or {}).get("posters") or [])
    if primary and not any((p.get("file_path") or "") == primary for p in posters):
        posters = [{"file_path": primary, "vote_average": 0, "vote_count": 0, "height": 0}] + posters
    posters.sort(key=lambda p: (float(p.get("vote_average") or 0), int(p.get("vote_count") or 0),
                                 int(p.get("height") or 0)), reverse=True)
    return posters


def fetch_poster_bytes(session: requests.Session, file_path: str) -> bytes | None:
    resp = session.get(f"{TMDB_IMG}{file_path}", timeout=20)
    if resp.status_code != 200 or not resp.content:
        return None
    return resp.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--variants-dir", default="data/posters_multi")
    ap.add_argument("--catalog-out", default="data/sample_output/multi_poster_catalog.csv")
    ap.add_argument("--langs", default="en,null")
    ap.add_argument("--max-per-id", type=int, default=5)
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1, help="split --in across N parallel shards (default 1: no sharding)")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = shard_rows(rows, args.shard_index, args.shard_count)

    variants_dir = Path(args.variants_dir)
    catalog_rows = []
    n_downloaded = 0

    for i, row in enumerate(rows, 1):
        pid = row["id"]
        movie_dir = variants_dir / pid
        if movie_dir.exists() and any(movie_dir.glob("*.jpg")):
            log.info(f"  {pid}: variants already downloaded, skipping")
            continue

        data = discover_images(session, api_key, pid, args.langs)
        posters = rank_posters(data)[: args.max_per_id]
        movie_dir.mkdir(parents=True, exist_ok=True)

        for p in posters:
            fp = p.get("file_path") or ""
            if not fp.startswith("/"):
                continue
            content = fetch_poster_bytes(session, fp)
            if not content:
                continue
            dest = movie_dir / (fp.lstrip("/"))
            dest.write_bytes(content)
            n_downloaded += 1
            catalog_rows.append({
                "id": pid, "title": row.get("title", ""), "file_path": fp,
                "vote_average": p.get("vote_average") or 0, "vote_count": p.get("vote_count") or 0,
                "height": p.get("height") or 0,
            })
            time.sleep(0.05)

        if i % 10 == 0 or i == len(rows):
            log.info(f"{i}/{len(rows)} movies, {n_downloaded} posters downloaded so far")
        time.sleep(0.05)

    write_csv_rows(args.catalog_out, catalog_rows)
    log.info(f"wrote {args.catalog_out} ({len(catalog_rows)} rows) and variant files to {variants_dir}")


if __name__ == "__main__":
    main()
