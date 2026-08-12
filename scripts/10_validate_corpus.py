#!/usr/bin/env python3
"""Orchestrates scripts 01-09 end to end and produces the three deliverables:
data/sample_output/validated_corpus.csv, excluded_ids.csv, qa_report.json.

Not locked to horror: --genre/--start-year/--end-year pass straight through
to 01_tmdb_enumerate.py, and --ids-path defaults to a genre-specific sample
file so a non-horror run doesn't collide with data/sample_input/sample_100_ids.csv.

  TMDB_API_KEY=... AWS_PROFILE=your-profile python3 10_validate_corpus.py --limit 100
  python3 10_validate_corpus.py --genre 878 --limit 100
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
from utils.constants import TMDB_HORROR_GENRE_ID
from utils.logging_setup import get_logger

log = get_logger("validate_corpus")
SCRIPTS_DIR = Path(__file__).parent


def run_step(name: str, args: list[str]) -> None:
    log.info(f"--- {name} ---")
    result = subprocess.run([sys.executable, str(SCRIPTS_DIR / name), *args])
    if result.returncode != 0:
        raise SystemExit(f"{name} failed (exit {result.returncode})")


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
    args = ap.parse_args()

    t0 = time.time()
    if args.ids_path:
        ids_path = args.ids_path
    elif args.genre == TMDB_HORROR_GENRE_ID:
        ids_path = "data/sample_input/sample_100_ids.csv"
    else:
        ids_path = f"data/sample_input/sample_100_ids_genre{args.genre}.csv"

    if not args.skip_enumerate:
        run_step("01_tmdb_enumerate.py", [
            "--genre", str(args.genre), "--start-year", str(args.start_year), "--end-year", str(args.end_year),
            "--limit", str(args.limit), "--out", ids_path,
        ])

    run_step("02_verify_poster_exists.py", ["--in", ids_path])
    run_step("03_match_imdb.py", ["--in", ids_path, *(["--akas", args.akas] if args.akas else [])])
    run_step("04_bedrock_ocr.py", ["--in", ids_path])
    run_step("05_comprehend_language.py", ["--in", "data/sample_output/vision_title_check.csv"])
    run_step("06_translate_titles.py", ["--in", "data/sample_output/language_detection.csv", "--titles", ids_path])
    run_step("07_dedupe_tmdb_metadata.py", ["--in", ids_path])
    run_step("08_dedupe_poster_md5.py", ["--in", ids_path])
    run_step("09_collapse_compilations.py", ["--in", "data/sample_output/vision_title_check.csv"])

    # -- assemble final outputs --
    with open(ids_path, newline="", encoding="utf-8") as f:
        catalog = {r["id"]: r for r in csv.DictReader(f)}

    excluded: dict[str, str] = {}

    ver_path = Path("data/sample_output/poster_verification.csv")
    if ver_path.exists():
        with ver_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["verified"] != "1":
                    excluded[r["id"]] = f"no_verifiable_poster:{r['reason']}"

    dup_path = Path("data/sample_output/duplicate_resolution.csv")
    if dup_path.exists():
        with dup_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["keep"] != "1" and r["id"] not in excluded:
                    excluded[r["id"]] = f"tmdb_duplicate:{r['resolution']}"

    md5_path = Path("data/sample_output/poster_md5_duplicates.csv")
    if md5_path.exists():
        with md5_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["keep"] != "1" and r["id"] not in excluded:
                    excluded[r["id"]] = f"poster_md5_dup:{r['reason']}"

    comp_path = Path("data/sample_output/compilation_groups.csv")
    if comp_path.exists():
        with comp_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["resolution"] == "compilation_entry_found" and r["segment_id"] != r["canonical_id"] and r["segment_id"] not in excluded:
                    excluded[r["segment_id"]] = f"collapsed_into_compilation:{r['canonical_title']}"

    validated = [row for mid, row in catalog.items() if mid not in excluded]

    out_dir = Path("data/sample_output")
    with (out_dir / "validated_corpus.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(catalog[next(iter(catalog))].keys()) if catalog else [])
        w.writeheader()
        w.writerows(validated)

    with (out_dir / "excluded_ids.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "reason"])
        for mid, reason in excluded.items():
            w.writerow([mid, catalog.get(mid, {}).get("title", ""), reason])

    report = {
        "total_input": len(catalog),
        "excluded": len(excluded),
        "validated": len(validated),
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    (out_dir / "qa_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    log.info(f"done: {report}")


if __name__ == "__main__":
    main()
