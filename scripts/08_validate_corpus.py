#!/usr/bin/env python3
"""Orchestrates scripts 01-07 end to end and produces the three deliverables:
data/sample_output/validated_corpus.csv, excluded_ids.csv, qa_report.json.

  TMDB_API_KEY=... AWS_PROFILE=your-profile python3 08_validate_corpus.py --limit 100
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
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--skip-enumerate", action="store_true", help="use an existing data/sample_input/sample_100_ids.csv instead of re-fetching")
    ap.add_argument("--akas", default=None, help="path to IMDb title.akas.tsv.gz (optional)")
    args = ap.parse_args()

    t0 = time.time()
    ids_path = "data/sample_input/sample_100_ids.csv"

    if not args.skip_enumerate:
        run_step("01_tmdb_enumerate.py", ["--limit", str(args.limit), "--out", ids_path])

    run_step("02_match_imdb.py", ["--in", ids_path, *(["--akas", args.akas] if args.akas else [])])
    run_step("03_bedrock_ocr.py", ["--in", ids_path])
    run_step("04_comprehend_language.py", ["--in", "data/sample_output/vision_title_check.csv"])
    run_step("05_translate_titles.py", ["--in", "data/sample_output/language_detection.csv", "--titles", ids_path])
    run_step("06_dedupe_tmdb_metadata.py", ["--in", ids_path])
    run_step("07_collapse_compilations.py", ["--in", "data/sample_output/vision_title_check.csv"])

    # -- assemble final outputs --
    with open(ids_path, newline="", encoding="utf-8") as f:
        catalog = {r["id"]: r for r in csv.DictReader(f)}

    excluded: dict[str, str] = {}

    dup_path = Path("data/sample_output/duplicate_resolution.csv")
    if dup_path.exists():
        with dup_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["keep"] != "1":
                    excluded[r["id"]] = f"tmdb_duplicate:{r['resolution']}"

    comp_path = Path("data/sample_output/compilation_groups.csv")
    if comp_path.exists():
        with comp_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["resolution"] == "compilation_entry_found" and r["segment_id"] != r["canonical_id"]:
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
