#!/usr/bin/env python3
"""Find posters shared across multiple catalog ids (a strong signal that
they're really segments of one VHS/DVD compilation or TV anthology, not
individually-posterized films), and check whether the compilation itself
has its own TMDB entry to collapse into.

Real example: 6 short films by the same director were each listed
separately in TMDB, but every one of them had the exact same "Sheets of
Gore" compilation cover as its poster. TMDB also had a *separate*, correct
entry for "Sheets of Gore" itself (with its own poster and metadata) — the
fix was collapsing the 6 segment ids into that one, not picking one of the
6 arbitrarily. Not every case has a rescuable id like that: when TMDB has
no entry for the compilation/anthology itself, this script reports the
group but does not auto-resolve it — that's a judgment call (see
docs/RESULTS.md, "Bite Size Halloween" / "Late Night Horror").

Score *every* TMDB search result against the shared OCR text (not just
bail out unless the search returns exactly one candidate) using the same
overlap+fuzzy title matching used elsewhere in this repo (see
`utils/text_match.py`), and keep the best-scoring one above a real
threshold -- not "any nonzero token overlap." Deliberately does NOT
exclude a candidate just because its id matches one of the segment ids in
the input group: the correct canonical entry can legitimately be one of
them (a real, live-checked case forced this -- see
docs/VALIDATION_LOGIC.md's "Deciding whether a shared poster is a
compilation" for the full story and why an earlier, untested version of
this function got that backwards).

Groups with no TMDB compilation entry get a second look before being left
as one ambiguous bucket: if the segment ids already have clearly distinct
real catalog titles, that's real evidence they're a genuine series/
franchise sharing one piece of stock art (not a mis-split compilation at
all), not something that needs a human "collapse vs. leave" call --
confirmed live 2026-08-17 on a real 43-group case (see docs/RESULTS.md,
"Gate 11's '43 unresolved' closed"): 42/43 had clearly distinct segment
titles (e.g. 106 real, individually-titled *Hazards of Helen* silent-
serial episodes sharing one generic title card) and only 1 was a genuine
title-normalization duplicate, not a compilation case either. Resolution
is split into `confirmed_not_compilation` (title diversity found --
correctly stays as separate entries, no review needed) vs.
`ambiguous_needs_review` (titles too similar/sparse to tell -- the real
remaining human-call bucket, much smaller than "everything without a
compilation entry").

  TMDB_API_KEY=... python3 11_collapse_compilations.py --in data/sample_output/vision_title_check.csv

Resumable: the TMDB search per shared-poster group is cached in --cache
(poster_path -> canonical_id/title/resolution), appended to on each run, so
an interrupted run doesn't re-search TMDB for groups it already resolved.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_tmdb_key
from utils.constants import COMPILATION_MATCH_THRESHOLD
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append, write_csv_rows
from utils.text_match import title_fuzzy_score, title_overlap_score
from utils.tmdb_client import tmdb_get

log = get_logger("collapse_compilations")


def _norm_title(t: str) -> str:
    return "".join(c for c in (t or "").lower() if c.isalnum())


def has_distinct_segment_titles(segment_titles: list[str]) -> bool:
    """Pure function: True if a shared-poster group's own segment titles
    are diverse enough to be real, separately-titled catalog entries
    (a series/franchise reusing stock art) rather than one work
    incorrectly split, or a title-normalization duplicate of itself.
    Threshold (>= max(2, 80% distinct)) and live numbers -- 42/43 real
    groups qualified, the 1 that didn't was a spacing-only duplicate of
    the same title -- come from docs/RESULTS.md, "Gate 11's '43
    unresolved' closed"."""
    titles = [t for t in segment_titles if t]
    if len(titles) < 2:
        return False
    n_distinct = len({_norm_title(t) for t in titles})
    return n_distinct >= max(2, int(len(titles) * 0.8))


def search_movie(session: requests.Session, api_key: str, query: str) -> list[dict]:
    if not query.strip():
        return []
    resp = tmdb_get(session, api_key, "search/movie", params={"query": query})
    if resp.status_code != 200:
        return []
    return resp.json().get("results", [])


def best_compilation_match(query_text: str, candidates: list[dict],
                            min_score: float = COMPILATION_MATCH_THRESHOLD) -> dict:
    """Pure function: pick the best-scoring TMDB search result for a
    shared-poster compilation/anthology, using max(overlap, fuzzy) title
    matching against every candidate -- not just requiring exactly one raw
    search result. Returns {"canonical_id", "canonical_title", "score"},
    id/title empty if nothing clears min_score. Does NOT exclude candidates
    that happen to be one of the segment ids in the input group -- the
    correct compilation entry can legitimately be one of them (see the
    real Sheets of Gore case in this script's docstring)."""
    best_id, best_title, best_score = "", "", 0.0
    for c in candidates:
        cid = str(c.get("id", ""))
        if not cid:
            continue
        title = c.get("title", "")
        score = max(title_overlap_score(query_text, title), title_fuzzy_score(query_text, title))
        if score > best_score:
            best_id, best_title, best_score = cid, title, score
    if best_score >= min_score:
        return {"canonical_id": best_id, "canonical_title": best_title, "score": best_score}
    return {"canonical_id": "", "canonical_title": "", "score": best_score}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_output/vision_title_check.csv")
    ap.add_argument("--poster-col", default="poster_path")
    ap.add_argument("--text-col", default="text_you_read")
    ap.add_argument("--out", default="data/sample_output/compilation_groups.csv")
    ap.add_argument("--cache", default="data/sample_output/.compilation_search_cache.csv",
                     help="poster_path->canonical id/title/resolution cache, resumed across runs")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_poster: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        p = r.get(args.poster_col, "").strip()
        if p:
            by_poster[p].append(r)

    shared = {p: items for p, items in by_poster.items() if len(items) > 1}
    log.info(f"posters shared by 2+ catalog ids: {len(shared)} groups, {sum(len(v) for v in shared.values())} ids")

    cache_path = Path(args.cache)
    cache_fields = ["poster_path", "shared_text", "canonical_id", "canonical_title", "match_score", "resolution"]
    done = load_done_ids(cache_path, id_col="poster_path")
    todo = {p: items for p, items in shared.items() if p not in done}
    if done:
        log.info(f"resuming: {len(done)} group(s) already searched, {len(todo)} remaining")

    cf, cw = open_for_append(cache_path, cache_fields)
    try:
        for poster, items in todo.items():
            query_text = items[0].get(args.text_col, "")
            candidates = search_movie(session, api_key, query_text)
            time.sleep(0.1)

            match = best_compilation_match(query_text, candidates)
            resolution = "compilation_entry_found" if match["canonical_id"] else "no_compilation_entry_found"

            cw.writerow({"poster_path": poster, "shared_text": query_text,
                         "canonical_id": match["canonical_id"], "canonical_title": match["canonical_title"],
                         "match_score": match["score"], "resolution": resolution})
    finally:
        cf.close()

    with cache_path.open(newline="", encoding="utf-8") as f:
        cache = {r["poster_path"]: r for r in csv.DictReader(f)}

    out_rows = []
    groups_by_resolution: dict[str, int] = defaultdict(int)
    for poster, items in shared.items():
        c = cache.get(poster, {})
        resolution = c.get("resolution", "no_compilation_entry_found")
        if resolution == "no_compilation_entry_found":
            segment_titles = [r.get("title", "") for r in items]
            resolution = ("confirmed_not_compilation" if has_distinct_segment_titles(segment_titles)
                          else "ambiguous_needs_review")
        groups_by_resolution[resolution] += 1
        for r in items:
            out_rows.append({
                "poster_path": poster, "segment_id": r["id"], "segment_title": r.get("title", ""),
                "shared_text": c.get("shared_text", ""), "canonical_id": c.get("canonical_id", ""),
                "canonical_title": c.get("canonical_title", ""), "match_score": c.get("match_score", ""),
                "resolution": resolution,
            })

    out_path = Path(args.out)
    write_csv_rows(out_path, out_rows)

    n_resolved = groups_by_resolution.get("compilation_entry_found", 0)
    n_confirmed_not = groups_by_resolution.get("confirmed_not_compilation", 0)
    n_ambiguous = groups_by_resolution.get("ambiguous_needs_review", 0)
    log.info(f"wrote {out_path} — {len(shared)} groups: "
             f"{n_resolved} had a rescuable compilation id in TMDB, "
             f"{n_confirmed_not} confirmed NOT a compilation (distinct real segment titles), "
             f"{n_ambiguous} still ambiguous and need a human call (see docs/RESULTS.md)")


if __name__ == "__main__":
    main()
