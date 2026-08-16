#!/usr/bin/env python3
"""Find exact-duplicate poster images by MD5 hash of the downloaded file.

Different from 07_dedupe_tmdb_metadata.py: that script catches the same
*film* listed twice under different ids (possibly with different posters).
This one catches the same *image file* used for two different catalog ids
-- often a franchise/series that reused stock art (see the real example
below), sometimes a genuine data error. A real example from the full run:
"Castle Ghosts of Ireland" and "Castle Ghosts of Wales" both used the exact
same poster file (same MD5) as "Castle Ghosts of England" -- almost
certainly a documentary series where only one episode had unique art and
the rest were stubbed with a placeholder. (Confirmed real: all three ids
and this exact grouping are still present in the real project's own data,
`data/master_dataset.csv` and `TODO_RESUME.md`.)

Where two or more ids share an MD5, keeps whichever entry is more
complete/curated, using `utils/tmdb_completeness.py`'s 4-signal cascade
(imdb_id present -> credits count -> trailer present -> popularity) -- the
same canonical "more complete entry" signal 07_dedupe_tmdb_metadata.py
uses, rather than a separate, weaker proxy (an earlier version of this
script used TMDB's `vote_count` alone) invented just for this gate. The
real project's own mechanism for this specific gate couldn't be verified
against source either way -- no script that computes poster MD5 hashes
exists in the local copy of the real project (its data/qa/ directory
references an already-computed `excluded_poster_md5_dup.csv`, and the
narrative doc that would name the real script, `docs/HISTORIAL_PROYECTO.md`
"Fase 11", lives on a currently-disconnected external drive) -- so rather
than guess at an unreproducible mechanism, this uses the same robust,
TMDB-grounded cascade as gate 7.

Resumable: downloading and hashing every poster is the slow part -- that
work is cached in --cache (id -> md5), appended to on each run, so an
interrupted run doesn't re-download posters it already hashed. The
completeness signals (needed only for ids that actually end up in a
confirmed MD5-duplicate group) are cached separately in --tmdb-cache. The
final grouping/resolution step is cheap and local, so it's always redone
in full from whatever's in both caches.

Depends on 02_verify_poster_exists.py having already run: --verified points
at its output, and any id it marked unverified is skipped here too, instead
of wasting a download attempt on a poster already known to be unreachable.
If --verified doesn't exist, falls back to just checking poster_path is
non-empty.

  TMDB_API_KEY=... python3 08_dedupe_poster_md5.py --in data/sample_input/sample_100_ids.csv
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
from utils.aws_config import get_tmdb_key
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append, write_csv_rows
from utils.tmdb_completeness import completeness_key, get_completeness_signals

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
    ap.add_argument("--tmdb-cache", default="data/sample_output/.tmdb_dedupe_cache.csv",
                     help="id->completeness-signal cache, shared format with 07's --cache, "
                          "resumed across runs")
    ap.add_argument("--verified", default="data/sample_output/poster_verification.csv",
                     help="output of 02_verify_poster_exists.py; ids not marked verified=1 there "
                          "are skipped here without attempting a download. Pass '' to disable.")
    args = ap.parse_args()

    api_key = get_tmdb_key()
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
    cache_fields = ["id", "title", "md5", "error"]
    done = load_done_ids(cache_path)
    todo = [row for row in rows if row["id"] not in done and row.get("poster_path")]
    if done:
        log.info(f"resuming: {len(done)} already hashed, {len(todo)} remaining")

    cf, cw = open_for_append(cache_path, cache_fields)
    try:
        for i, row in enumerate(todo, 1):
            try:
                h = poster_md5(session, row["poster_path"])
                cw.writerow({"id": row["id"], "title": row.get("title", ""), "md5": h, "error": ""})
            except Exception as e:
                log.info(f"  {row['id']}: fetch failed ({e})")
                cw.writerow({"id": row["id"], "title": row.get("title", ""), "md5": "", "error": str(e)[:200]})
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

    dup_ids = {r["id"] for items in dup_groups.values() for r in items}
    tmdb_cache_path = Path(args.tmdb_cache)
    tmdb_cache_fields = ["id", "alive", "credits", "has_imdb_id", "has_trailer", "popularity"]
    tmdb_done = load_done_ids(tmdb_cache_path)
    tmdb_todo = sorted(dup_ids - tmdb_done)
    if tmdb_done:
        log.info(f"completeness signals: resuming, {len(tmdb_done & dup_ids)} already fetched, "
                  f"{len(tmdb_todo)} remaining")

    tf, tw = open_for_append(tmdb_cache_path, tmdb_cache_fields)
    try:
        for i, movie_id in enumerate(tmdb_todo, 1):
            signals = get_completeness_signals(session, api_key, movie_id)
            time.sleep(0.3)
            tw.writerow({"id": movie_id, **signals})
            if i % 25 == 0:
                log.info(f"completeness signals: {i}/{len(tmdb_todo)}")
    finally:
        tf.close()

    with tmdb_cache_path.open(newline="", encoding="utf-8") as f:
        tmdb_cache = {r["id"]: r for r in csv.DictReader(f)}

    out_rows = []
    for h, items in dup_groups.items():
        keep = max(items, key=lambda r: completeness_key(tmdb_cache.get(r["id"], {})))["id"]
        for r in items:
            out_rows.append({"md5": h, "id": r["id"], "title": r.get("title", ""),
                              "keep": int(r["id"] == keep), "reason": "exact_poster_md5_dup"})

    out_path = Path(args.out)
    write_csv_rows(out_path, out_rows)

    log.info(f"wrote {out_path} ({len(out_rows)} rows)")


if __name__ == "__main__":
    main()
