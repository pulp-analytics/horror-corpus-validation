#!/usr/bin/env python3
"""Orchestrates scripts 01-09 end to end and produces the three deliverables:
data/sample_output/validated_corpus.csv, excluded_ids.csv, qa_report.json.

Not locked to horror: --genre/--start-year/--end-year pass straight through
to 01_tmdb_enumerate.py, and every intermediate/output file (not just the
enumerated sample) gets a genre-specific name for any non-default genre, so
running two genres against the same data/ directory never mixes their
results -- e.g. running --genre 878 after the default horror run produces
vision_title_check_genre878.csv alongside vision_title_check.csv, not on
top of it.

Fully programmatic: nothing here is left for a human to decide. Two cases
used to require manual review; both are now auto-excluded if the tools we
already ran can't resolve them --
  - a 05_bedrock_ocr.py "mismatch" verdict is kept only if the poster's OCR'd
    text (raw, or translated by 06) overlaps a candidate title -- the
    catalog title itself, or an alt title from 04_fetch_alt_titles.py --
    above ALT_TITLE_OVERLAP_THRESHOLD; otherwise excluded as
    unresolved_title_mismatch. ("no_title_on_poster" verdicts are NOT
    touched by this -- a title-less poster isn't evidence of anything wrong.)
  - a 10_collapse_compilations.py group with no rescuable canonical TMDB
    entry is excluded as unresolved_shared_poster, instead of being left
    unresolved.

Gates 7 (same film, different id), 8 (exact same poster image, different
id), and 9 (poster shared by 2+ ids -- is it a compilation) can all reach a
verdict on the same id, and their verdicts can disagree: gate 10's
TMDB-search-confirmed answer is strictly more informed than gates 7/8's
generic completeness proxies whenever it applies. compute_dedup_exclusions()
below gives gate 10 first say and protects any id it confirms as a
compilation's canonical entry from being excluded by gates 7/8. This
precedence was added after a real bug -- see docs/VALIDATION_LOGIC.md
("Deciding whether a shared poster is a compilation") for the full story
and the exact ids involved.

  TMDB_API_KEY=... AWS_PROFILE=your-profile python3 11_validate_corpus.py --limit 100
  python3 11_validate_corpus.py --genre 878 --limit 100

--assemble-only skips running 01-09 and jumps straight to reading their
outputs and writing the three deliverables. For running each script as its
own step in an external orchestrator (e.g. AWS Step Functions driving
Fargate tasks -- see the sibling poster-analysis-infrastructure repo), that
orchestrator runs 01-09 itself; this script's only job at that point is the
assembly logic, which is exactly what --assemble-only gives you.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.constants import ALT_TITLE_OVERLAP_THRESHOLD, TMDB_HORROR_GENRE_ID
from utils.logging_setup import get_logger
from utils.text_match import best_overlap

log = get_logger("validate_corpus")
SCRIPTS_DIR = Path(__file__).parent


def run_step(name: str, args: list[str]) -> None:
    log.info(f"--- {name} ---")
    result = subprocess.run([sys.executable, str(SCRIPTS_DIR / name), *args])
    if result.returncode != 0:
        raise SystemExit(f"{name} failed (exit {result.returncode})")


def compute_dedup_exclusions(comp_rows: list[dict], dup_rows: list[dict], md5_rows: list[dict]) -> dict[str, str]:
    """Pure function: merges gates 7 (metadata duplicate), 8 (poster MD5
    duplicate), and 9 (compilation collapse) into one id -> exclusion-reason
    map. Gate 10 goes first and any id it confirms as a compilation's
    canonical entry (a row where segment_id == canonical_id) is protected
    from exclusion by gates 7/8 -- they have no way to know a poster is
    actually a compilation from their own signals (completeness proxies,
    exact image match) alone, so their verdict for a protected id is
    simply wrong, not just lower-confidence. Every input row is assumed
    already filtered to catalog ids the caller cares about."""
    excluded: dict[str, str] = {}
    protected: set[str] = set()

    for r in comp_rows:
        if r["resolution"] != "compilation_entry_found":
            continue
        if r["segment_id"] == r["canonical_id"]:
            protected.add(r["segment_id"])
        else:
            excluded[r["segment_id"]] = f"collapsed_into_compilation:{r['canonical_title']}"
    for r in comp_rows:
        # no rescuable canonical TMDB entry for this shared poster -- can't
        # confirm it's right, so it doesn't get left as a manual judgment call
        if r["resolution"] == "no_compilation_entry_found" and r["segment_id"] not in excluded:
            excluded[r["segment_id"]] = "unresolved_shared_poster:no_compilation_entry_found"

    for r in dup_rows:
        if r["keep"] != "1" and r["id"] not in excluded and r["id"] not in protected:
            excluded[r["id"]] = f"tmdb_duplicate:{r['resolution']}"

    for r in md5_rows:
        if r["keep"] != "1" and r["id"] not in excluded and r["id"] not in protected:
            excluded[r["id"]] = f"poster_md5_dup:{r['reason']}"

    return excluded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--genre", type=int, default=TMDB_HORROR_GENRE_ID,
                     help="TMDB genre id (default: 27, Horror). Sci-Fi=878, Mystery=9648, Thriller=53, ...")
    ap.add_argument("--start-year", type=int, default=2020)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--ids-path", default=None,
                     help="where the enumerated sample lives (default: data/sample_input/sample_100_ids.csv "
                          "for the default horror genre, sample_100_ids_genre<id>.csv otherwise)")
    ap.add_argument("--skip-enumerate", action="store_true", help="use an existing --ids-path file instead of re-fetching")
    ap.add_argument("--akas", default=None, help="path to IMDb title.akas.tsv.gz (optional)")
    ap.add_argument("--assemble-only", action="store_true",
                     help="skip running 01-09 (assume something else already ran them, e.g. Step "
                          "Functions/Fargate tasks) and just read their outputs to assemble the "
                          "three deliverables")
    args = ap.parse_args()

    t0 = time.time()
    suffix = "" if args.genre == TMDB_HORROR_GENRE_ID else f"_genre{args.genre}"
    out_dir = Path("data/sample_output")

    def out(name: str, ext: str = "csv") -> str:
        return str(out_dir / f"{name}{suffix}.{ext}")

    if args.ids_path:
        ids_path = args.ids_path
    elif suffix:
        ids_path = f"data/sample_input/sample_100_ids{suffix}.csv"
    else:
        ids_path = "data/sample_input/sample_100_ids.csv"

    ver_path = out("poster_verification")
    vision_path = out("vision_title_check")
    lang_path = out("language_detection")

    if not args.assemble_only:
        if not args.skip_enumerate:
            run_step("01_tmdb_enumerate.py", [
                "--genre", str(args.genre), "--start-year", str(args.start_year), "--end-year", str(args.end_year),
                "--limit", str(args.limit), "--out", ids_path,
            ])

        run_step("03_verify_poster_exists.py", ["--in", ids_path, "--out", ver_path])
        run_step("04_fetch_alt_titles.py", ["--in", ids_path, "--out", out("alt_titles", "json"),
                                       *(["--akas", args.akas] if args.akas else [])])
        run_step("05_bedrock_ocr.py", ["--in", ids_path, "--out", vision_path, "--verified", ver_path])
        run_step("06_comprehend_language.py", ["--in", vision_path, "--out", lang_path])
        run_step("07_translate_titles.py", ["--in", lang_path, "--titles", ids_path, "--out", out("translated_titles")])
        run_step("08_dedupe_tmdb_metadata.py", ["--in", ids_path, "--out", out("duplicate_resolution"),
                                                 "--cache", str(out_dir / ".tmdb_dedupe_cache.csv")])
        run_step("09_dedupe_poster_md5.py", ["--in", ids_path, "--out", out("poster_md5_duplicates"),
                                              "--cache", str(out_dir / ".poster_md5_cache.csv"), "--verified", ver_path])
        run_step("10_collapse_compilations.py", ["--in", vision_path, "--out", out("compilation_groups"),
                                                  "--cache", str(out_dir / ".compilation_search_cache.csv")])
    else:
        log.info("--assemble-only: skipping 01-09, reading their outputs directly")

    # -- assemble final outputs --
    with open(ids_path, newline="", encoding="utf-8") as f:
        catalog = {r["id"]: r for r in csv.DictReader(f)}

    excluded: dict[str, str] = {}

    # Every gate below only records an exclusion for an id that's actually in
    # this run's catalog. A gate's output file can reference ids outside it
    # (e.g. a resumable --cache or a shared-poster group built from an older,
    # larger --in) -- without this guard those ids leak into excluded_ids.csv
    # with a blank title and inflate qa_report.json's count, even though
    # validated_corpus.csv (built by iterating `catalog`) was never affected.

    if Path(ver_path).exists():
        with open(ver_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["id"] in catalog and r["verified"] != "1":
                    excluded[r["id"]] = f"no_verifiable_poster:{r['reason']}"

    dup_path = out("duplicate_resolution")
    dup_rows = []
    if Path(dup_path).exists():
        with open(dup_path, newline="", encoding="utf-8") as f:
            dup_rows = [r for r in csv.DictReader(f) if r["id"] in catalog]

    md5_path = out("poster_md5_duplicates")
    md5_rows = []
    if Path(md5_path).exists():
        with open(md5_path, newline="", encoding="utf-8") as f:
            md5_rows = [r for r in csv.DictReader(f) if r["id"] in catalog]

    comp_path = out("compilation_groups")
    comp_rows = []
    if Path(comp_path).exists():
        with open(comp_path, newline="", encoding="utf-8") as f:
            comp_rows = [r for r in csv.DictReader(f) if r["segment_id"] in catalog]

    dedup_excluded = compute_dedup_exclusions(comp_rows, dup_rows, md5_rows)
    overlap = set(excluded) & set(dedup_excluded)
    if overlap:
        log.info(f"{len(overlap)} id(s) already excluded (no_verifiable_poster) before dedup gates ran, "
                 f"keeping that reason: {sorted(overlap)}")
    for id_, reason in dedup_excluded.items():
        excluded.setdefault(id_, reason)

    # unresolved title mismatches: a 04 "mismatch" verdict is only kept if the
    # poster's OCR'd text (raw or translated) overlaps the catalog title or one
    # of 03's alt titles above threshold -- otherwise we can't confirm the
    # poster is right, so it's excluded rather than left for manual review.
    # ("no_title_on_poster" verdicts are untouched -- absence of text isn't
    # evidence of a wrong poster.)
    alt_titles: dict[str, dict] = {}
    alt_path = Path(out("alt_titles", "json"))
    if alt_path.exists():
        alt_titles = json.loads(alt_path.read_text(encoding="utf-8"))

    translated: dict[str, str] = {}
    trans_path = out("translated_titles")
    if Path(trans_path).exists():
        with open(trans_path, newline="", encoding="utf-8") as f:
            translated = {r["id"]: r.get("translated", "") for r in csv.DictReader(f)}

    if Path(vision_path).exists():
        with open(vision_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                mid = r["id"]
                if mid not in catalog or r.get("verdict") != "mismatch" or mid in excluded:
                    continue
                alts = alt_titles.get(mid, {})
                candidates = [catalog.get(mid, {}).get("title", ""),
                              *alts.get("alt_titles_tmdb", []), *alts.get("alt_titles_imdb", [])]
                texts = [r.get("text_you_read", ""), translated.get(mid, "")]
                if best_overlap(texts, candidates) <= ALT_TITLE_OVERLAP_THRESHOLD:
                    excluded[mid] = f"unresolved_title_mismatch:{r.get('reason', '')[:80]}"

    validated = [row for mid, row in catalog.items() if mid not in excluded]

    with open(out("validated_corpus"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(catalog[next(iter(catalog))].keys()) if catalog else [])
        w.writeheader()
        w.writerows(validated)

    with open(out("excluded_ids"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "reason"])
        for mid, reason in excluded.items():
            w.writerow([mid, catalog.get(mid, {}).get("title", ""), reason])

    report = {
        "genre": args.genre,
        "total_input": len(catalog),
        "excluded": len(excluded),
        "validated": len(validated),
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    Path(out("qa_report", "json")).write_text(json.dumps(report, indent=2), encoding="utf-8")

    log.info(f"done: {report}")


if __name__ == "__main__":
    main()
