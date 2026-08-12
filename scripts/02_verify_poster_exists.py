#!/usr/bin/env python3
"""Verify each candidate actually has a reachable poster image before
spending any vision-LLM/OCR budget on it.

This is the single largest rejection category in the full run: rows where
TMDB's own `poster_path` is empty, or resolves to a 404, so any downstream
"analysis" of that poster is unreproducible -- there's no way to know what
image (if any) a prior process actually looked at. In the full corpus this
was ~90% of everything excluded (330 of 367 rows) -- much bigger than
duplicates or compilations, and easy to miss if you only look for the more
interesting-sounding problems.

Resumable: if --out already exists (e.g. an earlier run was interrupted),
already-verified ids are skipped and new results are appended -- re-running
after a crash doesn't re-spend API calls on rows you already have.

Shardable: --shard-index/--shard-count split --in's rows by position, so N
copies of this script can run in parallel (e.g. an AWS Batch array job)
each covering a disjoint slice -- every id here is checked independently,
so there's no cross-row state that sharding could break. Each shard needs
its own --out (they're separate files to merge afterward, not something
safe to have N processes append to concurrently).

  TMDB_API_KEY=... python3 02_verify_poster_exists.py --in data/sample_output/tmdb_horror_ids.csv
  python3 02_verify_poster_exists.py --shard-index 0 --shard-count 4 --out .../shard_0.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append, shard_rows

log = get_logger("verify_poster_exists")
TMDB_IMG = "https://image.tmdb.org/t/p/w92"  # small size -- we only need the status code


def poster_reachable(session: requests.Session, poster_path: str) -> bool:
    resp = session.head(f"{TMDB_IMG}{poster_path}", timeout=10)
    return resp.status_code == 200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/poster_verification.csv")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1, help="split --in across N parallel shards (default 1: no sharding)")
    args = ap.parse_args()

    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = shard_rows(rows, args.shard_index, args.shard_count)

    out_path = Path(args.out)
    fields = ["id", "title", "poster_path", "verified", "reason"]

    done = load_done_ids(out_path)
    todo = [row for row in rows if row["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo)} remaining")

    n_no_path, n_unreachable, n_ok = 0, 0, 0
    f, w = open_for_append(out_path, fields)
    try:
        for i, row in enumerate(todo, 1):
            poster_path = row.get("poster_path", "").strip()
            if not poster_path:
                verified, reason = False, "no_poster_path"
                n_no_path += 1
            else:
                try:
                    ok = poster_reachable(session, poster_path)
                except Exception:
                    ok = False
                if ok:
                    verified, reason = True, ""
                    n_ok += 1
                else:
                    verified, reason = False, "poster_url_unreachable"
                    n_unreachable += 1
            w.writerow({"id": row["id"], "title": row.get("title", ""), "poster_path": poster_path,
                        "verified": int(verified), "reason": reason})
            if i % 25 == 0:
                log.info(f"{i}/{len(todo)}")
            time.sleep(0.03)
    finally:
        f.close()

    log.info(f"wrote {out_path}: {n_ok} verified, {n_no_path} no poster_path, {n_unreachable} unreachable (this run)")


if __name__ == "__main__":
    main()
