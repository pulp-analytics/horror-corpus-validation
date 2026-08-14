#!/usr/bin/env python3
"""Find exact-duplicate poster images by MD5 hash of the downloaded file.

Different from 06_dedupe_tmdb_metadata.py: that script catches the same
*film* listed twice under different ids (possibly with different posters).
This one catches the same *image file* used for two different catalog ids
-- often a franchise/series that reused stock art (see the real example
below), sometimes a genuine data error. A real example from the full run:
"Castle Ghosts of Ireland" and "Castle Ghosts of Wales" both used the exact
same poster file (same MD5) as "Castle Ghosts of England" -- almost
certainly a documentary series where only one episode had unique art and
the rest were stubbed with a placeholder.

Where two or more ids share an MD5, keeps whichever has the most complete
metadata (vote_count) and flags the rest.

Resumable: downloading and hashing every poster is the slow part -- that
work is cached in --cache (id -> md5), appended to on each run, so an
interrupted run doesn't re-download posters it already hashed. The final
grouping step is cheap and local, so it's always redone in full from
whatever's in the cache.

Depends on 02_verify_poster_exists.py having already run: --verified points
at its output, and any id it marked unverified is skipped here too, instead
of wasting a download attempt on a poster already known to be unreachable.
If --verified doesn't exist, falls back to just checking poster_path is
non-empty.

  python3 08_dedupe_poster_md5.py --in data/sample_input/sample_100_ids.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append, write_csv_rows

log = get_logger("dedupe_poster_md5")
TMDB_IMG = "https://image.tmdb.org/t/p/w500"


def poster_md5(session: requests.Session, poster_path: str) -> str:
    resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=15)
    resp.raise_for_status()
    return hashlib.md5(resp.content).hexdigest()


def load_verified_ids(path: Path) -> set[str] | None:
    """Ids that 02_verify_poster_exists.py marked verified=1. Returns None
    (meaning "no filter, trust each row's own poster_path") if the file
    doesn't exist -- lets this script still run standalone."""
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return {row["id"] for row in csv.DictReader(f) if row.get("verified") == "1"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/poster_md5_duplicates.csv")
    ap.add_argument("--cache", default="data/sample_output/.poster_md5_cache.csv",
                     help="id->md5 cache, resumed across runs")
    ap.add_argument("--verified", default="data/sample_output/poster_verification.csv",
                     help="output of 02_verify_poster_exists.py; ids not marked verified=1 there "
                          "are skipped here without attempting a download. Pass '' to disable.")
    args = ap.parse_args()

    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    verified_ids = load_verified_ids(Path(args.verified)) if args.verified else None
    if verified_ids is not None:
        n_before = len(rows)
        rows = [row for row in rows if row["id"] in verified_ids]
        log.info(f"filtered by {args.verified}: {len(rows)}/{n_before} have a verified poster")
    else:
        log.info(f"no --verified file at {args.verified!r}, falling back to a bare poster_path check")

    cache_path = Path(args.cache)
    cache_fields = ["id", "title", "vote_count", "md5", "error"]
    done = load_done_ids(cache_path)
    todo = [row for row in rows if row["id"] not in done and row.get("poster_path")]
    if done:
        log.info(f"resuming: {len(done)} already hashed, {len(todo)} remaining")

    cf, cw = open_for_append(cache_path, cache_fields)
    try:
        for i, row in enumerate(todo, 1):
            try:
                h = poster_md5(session, row["poster_path"])
                cw.writerow({"id": row["id"], "title": row.get("title", ""),
                             "vote_count": row.get("vote_count", ""), "md5": h, "error": ""})
            except Exception as e:
                log.info(f"  {row['id']}: fetch failed ({e})")
                cw.writerow({"id": row["id"], "title": row.get("title", ""),
                             "vote_count": row.get("vote_count", ""), "md5": "", "error": str(e)[:200]})
            if i % 25 == 0:
                log.info(f"{i}/{len(todo)}")
            time.sleep(0.05)
    finally:
        cf.close()

    with cache_path.open(newline="", encoding="utf-8") as f:
        cached = list(csv.DictReader(f))

    by_md5: dict[str, list[dict]] = defaultdict(list)
    for r in cached:
        if r.get("md5"):
            by_md5[r["md5"]].append(r)

    dup_groups = {h: items for h, items in by_md5.items() if len(items) > 1}
    log.info(f"exact-duplicate poster groups: {len(dup_groups)}")

    out_rows = []
    for h, items in dup_groups.items():

        def vote_count(r: dict) -> float:
            try:
                return float(r.get("vote_count") or 0)
            except ValueError:
                return 0.0

        keep = max(items, key=vote_count)["id"]
        for r in items:
            out_rows.append({"md5": h, "id": r["id"], "title": r.get("title", ""),
                              "keep": int(r["id"] == keep), "reason": "exact_poster_md5_dup"})

    out_path = Path(args.out)
    write_csv_rows(out_path, out_rows)

    log.info(f"wrote {out_path} ({len(out_rows)} rows)")


if __name__ == "__main__":
    main()
