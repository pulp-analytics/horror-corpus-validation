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

log = get_logger("dedupe_poster_md5")
TMDB_IMG = "https://image.tmdb.org/t/p/w500"


def poster_md5(session: requests.Session, poster_path: str) -> str:
    resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=15)
    resp.raise_for_status()
    return hashlib.md5(resp.content).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/poster_md5_duplicates.csv")
    args = ap.parse_args()

    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_md5: dict[str, list[dict]] = defaultdict(list)
    for i, row in enumerate(rows, 1):
        if row.get("poster_path"):
            try:
                h = poster_md5(session, row["poster_path"])
                by_md5[h].append(row)
            except Exception as e:
                log.info(f"  {row['id']}: fetch failed ({e})")
        if i % 25 == 0:
            log.info(f"{i}/{len(rows)}")
        time.sleep(0.05)

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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_rows:
        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)

    log.info(f"wrote {out_path} ({len(out_rows)} rows)")


if __name__ == "__main__":
    main()
