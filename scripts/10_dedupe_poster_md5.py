#!/usr/bin/env python3
"""Find exact-duplicate poster images by MD5 hash of the downloaded file.

Different from 09_dedupe_tmdb_metadata.py: that script catches the same
*film* listed twice under different ids (possibly with different posters).
This one catches the same *image file* used for two different catalog ids
-- often a franchise/series that reused stock art, sometimes a genuine
data error (real example: a documentary series with only one episode's
poster art, the rest stubbed with a placeholder -- see docs/RESULTS.md).

Where two or more ids share an MD5, keeps whichever entry is more
complete/curated, using `utils/tmdb_completeness.py`'s 4-signal cascade
(imdb_id present -> credits count -> trailer present -> popularity) -- the
same canonical "more complete entry" signal 09_dedupe_tmdb_metadata.py
uses, rather than a separate, weaker proxy invented just for this gate.
See docs/VALIDATION_LOGIC.md ("Deciding whether two ids are really
duplicates") for why this cascade exists instead of trying to reproduce
the real project's own (unreproducible) tiebreaker.

This gate's cascade is a generic "which entry is richer" answer, and
that's the wrong question when the shared poster is actually a
compilation rather than a franchise reusing stock art -- it has no way to
know that from MD5 + completeness alone. 11_collapse_compilations.py's
TMDB-search-verified resolution is the more-informed answer for that
case; 12_validate_corpus.py's compute_dedup_exclusions() is what actually
arbitrates between the two gates when both fire on the same poster_path.
Full story (a real bug this found and how it was fixed) in
docs/VALIDATION_LOGIC.md.

Resumable: downloading and hashing every poster is the slow part -- that
work is cached in --cache (id -> md5), appended to on each run, so an
interrupted run doesn't re-download posters it already hashed. The
completeness signals (needed only for ids that actually end up in a
confirmed MD5-duplicate group) are cached separately in --tmdb-cache. The
final grouping/resolution step is cheap and local, so it's always redone
in full from whatever's in both caches.

Depends on 03_verify_poster_exists.py having already run: --verified points
at its output, and any id it marked unverified is skipped here too, instead
of wasting a download attempt on a poster already known to be unreachable.
If --verified doesn't exist, falls back to just checking poster_path is
non-empty.

  TMDB_API_KEY=... python3 10_dedupe_poster_md5.py --in data/sample_input/sample_100_ids.csv
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
from utils.resumable import open_for_append, shard_rows, write_csv_rows
from utils.tmdb_client import IMAGE_BASE_URL
from utils.tmdb_completeness import completeness_key, get_completeness_signals

log = get_logger("dedupe_poster_md5")
TMDB_IMG = f"{IMAGE_BASE_URL}w500"


def poster_md5(session: requests.Session, poster_path: str) -> str:
    resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=15)
    resp.raise_for_status()
    return hashlib.md5(resp.content).hexdigest()


def load_done_ids(path: Path, retry_errors: bool = False) -> set[str]:
    """Like utils.resumable.load_done_ids, but optionally treats rows that
    errored last time (empty md5, non-empty error) as NOT done, so a
    re-run retries just those instead of leaving that id's poster
    permanently un-hashed -- a real transient TMDB image-CDN timeout did
    exactly this to one id in a live run, silently under-counting its
    duplicate group by one until the row was cleared and rehashed by
    hand. Same --retry-errors pattern as 06_bedrock_ocr.py."""
    if not path.exists():
        return set()
    done = set()
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if retry_errors and row.get("error"):
                continue
            if row.get("id"):
                done.add(row["id"])
    return done


def load_verified_ids(path: Path) -> set[str] | None:
    """Ids that 03_verify_poster_exists.py marked verified=1. Returns None
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
                     help="output of 03_verify_poster_exists.py; ids not marked verified=1 there "
                          "are skipped here without attempting a download. Pass '' to disable.")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1,
                     help="split --in across N parallel shards for the download+hash phase "
                          "(default 1: no sharding). The final group-by-md5 step needs the "
                          "*global* set of hashes, so it only sees what's in --cache -- run N "
                          "shards each with their own --cache, merge the cache files, then run "
                          "once more with --shard-count 1 and --cache pointed at the merged file "
                          "to get a correct final grouping (todo will be empty, so it goes "
                          "straight to grouping).")
    ap.add_argument("--retry-errors", action="store_true",
                     help="on resume, redo ids that errored last time (in either the poster-hash "
                          "or completeness-signal cache) instead of leaving them permanently "
                          "un-hashed")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = shard_rows(rows, args.shard_index, args.shard_count)

    verified_ids = load_verified_ids(Path(args.verified)) if args.verified else None
    if verified_ids is not None:
        n_before = len(rows)
        rows = [row for row in rows if row["id"] in verified_ids]
        log.info(f"filtered by {args.verified}: {len(rows)}/{n_before} have a verified poster")
    else:
        log.info(f"no --verified file at {args.verified!r}, falling back to a bare poster_path check")

    cache_path = Path(args.cache)
    cache_fields = ["id", "title", "md5", "error"]
    done = load_done_ids(cache_path, args.retry_errors)
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
