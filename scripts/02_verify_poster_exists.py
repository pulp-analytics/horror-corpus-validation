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

  TMDB_API_KEY=... python3 02_verify_poster_exists.py --in data/sample_output/tmdb_horror_ids.csv
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

log = get_logger("verify_poster_exists")
TMDB_IMG = "https://image.tmdb.org/t/p/w92"  # small size -- we only need the status code


def poster_reachable(session: requests.Session, poster_path: str) -> bool:
    resp = session.head(f"{TMDB_IMG}{poster_path}", timeout=10)
    return resp.status_code == 200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/poster_verification.csv")
    args = ap.parse_args()

    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "title", "poster_path", "verified", "reason"]

    n_no_path, n_unreachable, n_ok = 0, 0, 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, row in enumerate(rows, 1):
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
                log.info(f"{i}/{len(rows)}")
            time.sleep(0.03)

    log.info(f"wrote {out_path}: {n_ok} verified, {n_no_path} no poster_path, {n_unreachable} unreachable")


if __name__ == "__main__":
    main()
