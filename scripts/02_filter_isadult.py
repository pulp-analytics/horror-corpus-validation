#!/usr/bin/env python3
"""Filter out real adult-content titles before spending any further budget
on them -- the earliest possible cut in the pipeline, right after gate 01's
enumeration and before any of the poster/OCR/dedup/moderation gates run.

01_tmdb_enumerate.py already asks TMDB's /discover/movie for
include_adult=false, but that flag has effectively zero real-world variance
in this corpus -- TMDB's own per-movie `adult` field is almost always
false, including on titles that plainly are adult content, so it doesn't
actually filter anything. The signal that does work is IMDb's own isAdult
column, from the free title.basics.tsv.gz bulk dataset (a different IMDb
file than the title.akas.tsv.gz one 05_fetch_alt_titles.py uses, same
non-commercial dataset). On the real corpus, cross-checking against it
found real hits that this project's own visual content-moderation gate
(15_content_moderation.py) later independently flagged too -- two unrelated
signals (title metadata vs. poster pixels) agreeing is what makes this a
real finding, not a fluke.

IMDb's file is keyed by its own id (a "tt..." tconst), not TMDB's, so this
fetches each row's imdb_id from TMDB's external_ids endpoint the same way
05_fetch_alt_titles.py does (one extra API call per id) -- deliberately not
shared/coupled with that script's own fetch, so this gate can run
standalone, first, before anything downstream needs to exist yet.

Rows with no resolvable imdb_id (no IMDb entry, or --basics not given at
all) pass through unfiltered -- absence of the signal isn't evidence of
adult content, and this project doesn't want to silently drop titles it
has no real basis to drop. See docs/AWS_SETUP.md for where to download
title.basics.tsv.gz.

  TMDB_API_KEY=... python3 02_filter_isadult.py --in data/sample_output/tmdb_horror_ids.csv
  python3 02_filter_isadult.py --basics /path/to/title.basics.tsv.gz --in ...

Resumable: --out already has rows for ids we've already checked are
skipped on a re-run. Shardable: --shard-index/--shard-count split --in's
rows by position, same convention as every other gate in this repo.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_tmdb_key
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append, shard_rows
from utils.tmdb_client import tmdb_get

log = get_logger("filter_isadult")


def fetch_imdb_id(session: requests.Session, api_key: str, movie_id: str) -> str:
    resp = tmdb_get(session, api_key, f"movie/{movie_id}/external_ids")
    if resp.status_code != 200:
        return ""
    return resp.json().get("imdb_id") or ""


def load_adult_tconsts(basics_path: Path, tconsts: set[str]) -> set[str]:
    """One streaming pass over title.basics.tsv.gz, keeping only the
    tconsts we actually need that are flagged isAdult=1."""
    out: set[str] = set()
    with gzip.open(basics_path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split("\t")
        idx = {h: i for i, h in enumerate(header)}
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= idx["isAdult"]:
                continue
            tconst = parts[idx["tconst"]]
            if tconst in tconsts and parts[idx["isAdult"]] == "1":
                out.add(tconst)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/isadult_filter.csv")
    ap.add_argument("--prune-out", default=None,
                     help="if given, write --in's rows minus any is_adult=1 id to this path -- the pruned "
                          "catalog for downstream gates to use as their own --in, so they never spend real "
                          "budget on an id gate 2 already excluded. See 12_validate_corpus.py and "
                          "docs/RESULTS.md, 'Gate 9/10/11 sequencing.'")
    ap.add_argument("--basics", type=Path, default=None, help="path to IMDb title.basics.tsv.gz (optional)")
    ap.add_argument("--imdb-id-col", default="imdb_id", help="column with tt... ids, if --in already has one")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1, help="split --in across N parallel shards (default 1: no sharding)")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = shard_rows(rows, args.shard_index, args.shard_count)

    out_path = Path(args.out)
    fields = ["id", "title", "imdb_id", "is_adult", "reason"]

    done = load_done_ids(out_path)
    todo = [row for row in rows if row["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo)} remaining")

    imdb_ids: dict[str, str] = {}
    n_fetched = 0
    for i, row in enumerate(todo, 1):
        tt = (row.get(args.imdb_id_col) or "").strip()
        if not tt:
            tt = fetch_imdb_id(session, api_key, row["id"])
            n_fetched += 1
            time.sleep(0.1)
        imdb_ids[row["id"]] = tt
        if i % 100 == 0:
            log.info(f"imdb_id lookup {i}/{len(todo)} ({n_fetched} fetched, rest already had one)")

    adult_tconsts: set[str] = set()
    tconsts = {tt for tt in imdb_ids.values() if tt}
    if args.basics and args.basics.exists() and tconsts:
        log.info(f"scanning {args.basics} for {len(tconsts):,} tconsts...")
        adult_tconsts = load_adult_tconsts(args.basics, tconsts)
        log.info(f"isAdult=1 matches: {len(adult_tconsts)}")
    else:
        log.info("no --basics file given -- every row passes through unfiltered (see docs/AWS_SETUP.md)")

    n_adult = n_no_signal = n_clean = 0
    f, w = open_for_append(out_path, fields)
    try:
        for row in todo:
            tt = imdb_ids.get(row["id"], "")
            if not tt:
                is_adult, reason = 0, "no_imdb_id"
                n_no_signal += 1
            elif tt in adult_tconsts:
                is_adult, reason = 1, "imdb_isadult"
                n_adult += 1
            else:
                is_adult, reason = 0, ""
                n_clean += 1
            w.writerow({"id": row["id"], "title": row.get("title", ""), "imdb_id": tt,
                        "is_adult": is_adult, "reason": reason})
    finally:
        f.close()

    log.info(f"wrote {out_path}: {n_adult} filtered (isAdult), {n_no_signal} no imdb_id (passed through), "
              f"{n_clean} clean (this run)")

    if args.prune_out:
        # Re-read --in fresh (not the possibly-sharded `rows` above) and
        # --out's full, resumed verdict set (not just this run's todo) --
        # --prune-out should reflect every id gate 2 has ever judged, not
        # just this invocation's slice.
        with open(args.in_path, newline="", encoding="utf-8") as f:
            full_rows = list(csv.DictReader(f))
        with out_path.open(newline="", encoding="utf-8") as f:
            adult_ids = {r["id"] for r in csv.DictReader(f) if r.get("is_adult") == "1"}
        pruned = [r for r in full_rows if r["id"] not in adult_ids]
        prune_path = Path(args.prune_out)
        prune_path.parent.mkdir(parents=True, exist_ok=True)
        with prune_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(full_rows[0].keys()) if full_rows else [])
            w.writeheader()
            w.writerows(pruned)
        log.info(f"wrote {prune_path}: {len(pruned)}/{len(full_rows)} ids remain after pruning {len(adult_ids)} isAdult")


if __name__ == "__main__":
    main()
