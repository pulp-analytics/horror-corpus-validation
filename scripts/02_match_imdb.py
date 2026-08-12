#!/usr/bin/env python3
"""Fetch alternative titles per movie: TMDB's alternative_titles endpoint
(always available, no download needed) plus an optional cross-check against
IMDb's title.akas.tsv.gz if you have it locally (see docs/AWS_SETUP.md —
IMDb non-commercial datasets, not an AWS resource, but documented there
alongside the other one-time setup steps).

This is what let us tell a real "wrong poster" apart from "right poster,
foreign/reissue title" (see docs/RESULTS.md for the concrete case: a poster
reading "World of the Living Dead" that turned out to be a real AKA of the
catalog title, not a mismatch).

  TMDB_API_KEY=... python3 02_match_imdb.py --in data/sample_output/tmdb_horror_ids.csv
  python3 02_match_imdb.py --akas /path/to/title.akas.tsv.gz --in ...
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_tmdb_key
from utils.logging_setup import get_logger

log = get_logger("match_imdb")
ALT_URL = "https://api.themoviedb.org/3/movie/{id}/alternative_titles"


def fetch_tmdb_alts(session: requests.Session, api_key: str, movie_id: str) -> list[str]:
    resp = session.get(ALT_URL.format(id=movie_id), params={"api_key": api_key}, timeout=15)
    if resp.status_code != 200:
        return []
    return [t["title"] for t in resp.json().get("titles", []) if t.get("title")]


def load_akas_for_tconsts(akas_path: Path, tconsts: set[str]) -> dict[str, list[str]]:
    """One streaming pass over title.akas.tsv.gz, keeping only rows for our ids."""
    out: dict[str, list[str]] = {}
    with gzip.open(akas_path, "rt", encoding="utf-8", errors="replace") as f:
        next(f)  # header
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            tconst, _ordering, title, _region, _lang, _types, _attrs, is_original = parts
            if tconst not in tconsts or is_original == "1" or not title.strip():
                continue
            out.setdefault(tconst, []).append(title.strip())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/alt_titles.json")
    ap.add_argument("--akas", type=Path, default=None, help="path to IMDb title.akas.tsv.gz (optional)")
    ap.add_argument("--imdb-id-col", default="imdb_id", help="column with tt... ids, if present")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    tmdb_alts: dict[str, list[str]] = {}
    for i, row in enumerate(rows, 1):
        tmdb_alts[row["id"]] = fetch_tmdb_alts(session, api_key, row["id"])
        if i % 25 == 0:
            log.info(f"TMDB alt titles {i}/{len(rows)}")
        time.sleep(0.1)
    log.info(f"TMDB alt titles: {sum(1 for v in tmdb_alts.values() if v)}/{len(rows)} with >=1 alt")

    imdb_alts: dict[str, list[str]] = {}
    if args.akas and args.akas.exists():
        tconsts = {row[args.imdb_id_col] for row in rows if row.get(args.imdb_id_col)}
        if tconsts:
            log.info(f"scanning {args.akas} for {len(tconsts)} tconsts...")
            by_tconst = load_akas_for_tconsts(args.akas, tconsts)
            for row in rows:
                tt = row.get(args.imdb_id_col)
                if tt:
                    imdb_alts[row["id"]] = by_tconst.get(tt, [])
            log.info(f"IMDb AKAs: {sum(1 for v in imdb_alts.values() if v)}/{len(tconsts)} with >=1 AKA")
    else:
        log.info("no --akas file given, skipping IMDb cross-check (TMDB alt titles only)")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined = {
        row["id"]: {"alt_titles_tmdb": tmdb_alts.get(row["id"], []), "alt_titles_imdb": imdb_alts.get(row["id"], [])}
        for row in rows
    }
    out_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")
    log.info(f"wrote {out_path}")


if __name__ == "__main__":
    main()
