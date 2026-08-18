#!/usr/bin/env python3
"""Orchestrates scripts 01-11 (plus 13-14's gate 4 rescue) end to end and
produces the three deliverables: data/sample_output/validated_corpus.csv,
excluded_ids.csv, qa_report.json.

Sequencing (2026-08-18): gate 2 (isAdult) runs right after gate 1 -- the
cheapest possible cut, and pruned out of every downstream --in list before
gates 3-11 ever see those ids (real savings, no correctness risk -- isAdult
has no interaction with gate 11's compilation-protection override below).
Gates 9 (TMDB metadata duplicate) and 10 (poster MD5 duplicate) also moved
earlier in *execution order* (9 needs only gate 1's catalog columns; 10
needs gate 3 + gate 9's cache, not gates 4-8) -- but their exclusions are
still decided only at final assembly, same as before, NOT pruned out of
gates 3-8's --in: gate 11 needs to see every id gate 6 ran on to be able to
override a gate 9/10 exclusion for an id that turns out to be a
compilation's real canonical entry (the exact bug class
docs/VALIDATION_LOGIC.md already documents once -- pruning gates 3-8 by
gate 9/10's verdict would silently reintroduce it). See docs/RESULTS.md,
"Gate 9/10/11 sequencing and gate 4's rescue."

Not locked to horror: --genre/--start-year/--end-year pass straight through
to 01_tmdb_enumerate.py, and every intermediate/output file (not just the
enumerated sample) gets a genre-specific name for any non-default genre, so
running two genres against the same data/ directory never mixes their
results -- e.g. running --genre 878 after the default horror run produces
vision_title_check_genre878.csv alongside vision_title_check.csv, not on
top of it.

Fully programmatic: nothing here is left for a human to decide. Three cases
used to require manual review (or, for the third, weren't acted on at all);
all are now auto-excluded if the tools we already ran can't resolve them --
  - a 06_bedrock_ocr.py "mismatch" verdict is kept only if the poster's OCR'd
    text (raw, or translated by 06) overlaps a candidate title -- the
    catalog title itself, or an alt title from 05_fetch_alt_titles.py --
    above ALT_TITLE_OVERLAP_THRESHOLD; otherwise excluded as
    unresolved_title_mismatch. ("no_title_on_poster" verdicts are NOT
    touched by this -- a title-less poster isn't evidence of anything wrong.)
  - a 11_collapse_compilations.py group with no rescuable canonical TMDB
    entry is excluded as unresolved_shared_poster, instead of being left
    unresolved.
  - a 04_filter_poster_type.py is_movie_poster=False verdict now runs the
    same alternate-poster rescue gates 13-14 already do for title
    mismatches, before excluding: 13_find_alternate_posters.py fetches
    every other poster TMDB has for that id, then 14_score_alternate_
    posters.py --mode poster-type asks gate 4's own question of each one
    ("is this real poster art") instead of scoring title overlap. No TMDB
    alternates at all, or none that pass, both exclude as
    unresolved_not_a_poster; a rescued id is kept. This closes a gap this
    repo's own docs/RESULTS.md flagged and quantified but never wired up
    (an earlier live test found this rescues ~8.9% of zero-OCR-text
    "not a poster" candidates) -- see docs/RESULTS.md, "Gate 4's
    alternate-poster rescue."

Gates 9 (same film, different id), 10 (exact same poster image, different
id), and 11 (poster shared by 2+ ids -- is it a compilation) can all reach a
verdict on the same id, and their verdicts can disagree: gate 11's
TMDB-search-confirmed answer is strictly more informed than gates 9/10's
generic completeness proxies whenever it applies. compute_dedup_exclusions()
below gives gate 11 first say and protects any id it confirms as a
compilation's canonical entry from being excluded by gates 9/10. This
precedence was added after a real bug -- see docs/VALIDATION_LOGIC.md
("Deciding whether a shared poster is a compilation") for the full story
and the exact ids involved.

  TMDB_API_KEY=... AWS_PROFILE=your-profile python3 12_validate_corpus.py --limit 100
  python3 12_validate_corpus.py --genre 878 --limit 100

--assemble-only skips running 02-11 (+13-14's gate 4 rescue) and jumps
straight to reading their outputs and writing the three deliverables. For
running each script as its own step in an external orchestrator (e.g. AWS
Step Functions driving Fargate tasks -- see the sibling
poster-analysis-infrastructure repo), that orchestrator runs those steps
itself; this script's only job at that point is the assembly logic, which
is exactly what --assemble-only gives you. NOTE (2026-08-18): the deployed
state machine in that sibling repo predates gates 2, 4, and 13-14's rescue
being wired in here -- it only runs the original 9-script DAG (now gates
1/3/5/6/7/8/9/10/11 under current numbering) and needs a matching update
before --assemble-only's inputs there will be complete. Not yet done --
flagged here rather than silently left inconsistent.
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
    map. Gate 11 goes first and any id it confirms as a compilation's
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
    ap.add_argument("--basics", default=None, help="path to IMDb title.basics.tsv.gz for gate 2's isAdult "
                     "check (optional -- without it every row passes through unfiltered, see 02_filter_isadult.py)")
    ap.add_argument("--assemble-only", action="store_true",
                     help="skip running 02-11 (+13-14's gate 4 rescue) (assume something else "
                          "already ran them, e.g. Step Functions/Fargate tasks) and just read "
                          "their outputs to assemble the three deliverables")
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

    isadult_path = out("isadult_filter")
    pruned_ids_path = out("sample_ids_post_isadult")
    ver_path = out("poster_verification")
    vision_path = out("vision_title_check")
    lang_path = out("language_detection")
    poster_type_path = out("poster_type_filter")
    rescue_candidates_path = out("poster_type_rescue_candidates")
    rescue_variants_dir = str(Path("data") / f"posters_multi_poster_type{suffix}")
    rescue_scores_path = out("poster_type_rescue_scores")
    rescue_swaps_path = out("poster_type_rescue_swaps")

    if not args.assemble_only:
        if not args.skip_enumerate:
            run_step("01_tmdb_enumerate.py", [
                "--genre", str(args.genre), "--start-year", str(args.start_year), "--end-year", str(args.end_year),
                "--limit", str(args.limit), "--out", ids_path,
            ])

        # Gate 2 (isAdult) first -- the cheapest possible cut, and the only
        # dedup/filter gate whose exclusion is safe to prune out of every
        # downstream --in list. Unlike gates 9/10 (below), isAdult has no
        # interaction with gate 11's compilation-protection override, so
        # there's no equivalent of the "a gate 9-flagged id might actually
        # be a compilation's rescuable canonical entry" risk to preserve.
        # --prune-out does the filtering (same flag the Step Functions
        # version of this pipeline uses, so the two orchestrators share one
        # implementation instead of two).
        run_step("02_filter_isadult.py", ["--in", ids_path, "--out", isadult_path, "--prune-out", pruned_ids_path,
                                           *(["--basics", args.basics] if args.basics else [])])

        # Gate 9 has no dependency on gates 3-8 (only needs title/year/
        # overview, already in ids_path) -- runs here for clarity of
        # sequence, NOT to prune pruned_ids_path: its exclusion is decided
        # only at final assembly (below), same as before, so gate 11 can
        # still override it for a duplicate-flagged id that turns out to be
        # a compilation's real canonical entry. See docs/RESULTS.md, "Gate
        # 9/10 sequencing."
        run_step("09_dedupe_tmdb_metadata.py", ["--in", pruned_ids_path, "--out", out("duplicate_resolution"),
                                                 "--cache", str(out_dir / ".tmdb_dedupe_cache.csv")])

        run_step("03_verify_poster_exists.py", ["--in", pruned_ids_path, "--out", ver_path])

        # Gate 10 only needs gate 3 (verified) + gate 9's cache, not gates
        # 4-8 -- same "runs here for sequence clarity, not pruning" reasoning
        # as gate 9 above.
        run_step("10_dedupe_poster_md5.py", ["--in", pruned_ids_path, "--out", out("poster_md5_duplicates"),
                                              "--cache", str(out_dir / ".poster_md5_cache.csv"), "--verified", ver_path])

        run_step("04_filter_poster_type.py", ["--in", pruned_ids_path, "--out", poster_type_path,
                                               "--rescue-out", rescue_candidates_path])
        run_step("05_fetch_alt_titles.py", ["--in", pruned_ids_path, "--out", out("alt_titles", "json"),
                                       *(["--akas", args.akas] if args.akas else [])])
        run_step("06_bedrock_ocr.py", ["--in", pruned_ids_path, "--out", vision_path, "--verified", ver_path])
        run_step("07_comprehend_language.py", ["--in", vision_path, "--out", lang_path])
        run_step("08_translate_titles.py", ["--in", lang_path, "--titles", pruned_ids_path, "--out", out("translated_titles")])
        run_step("11_collapse_compilations.py", ["--in", vision_path, "--out", out("compilation_groups"),
                                                  "--cache", str(out_dir / ".compilation_search_cache.csv")])

        # Gate 4's alternate-poster rescue: gate 4's own --rescue-out already
        # wrote the not-a-poster candidate list above; run gates 13-14 in
        # poster-type mode against just that set. A separate --variants-dir
        # from title-mismatch's rescue keeps the two id populations'
        # downloaded variant files from colliding if a run ever needed both
        # (it currently doesn't -- gate 6 mismatches and gate 4 not-a-poster
        # verdicts are disjoint id sets by construction).
        rescue_rows = []
        if Path(rescue_candidates_path).exists():
            with open(rescue_candidates_path, newline="", encoding="utf-8") as f:
                rescue_rows = list(csv.DictReader(f))

        if rescue_rows:
            run_step("13_find_alternate_posters.py", ["--in", rescue_candidates_path, "--variants-dir", rescue_variants_dir,
                                                        "--catalog-out", out("poster_type_rescue_catalog")])
            run_step("14_score_alternate_posters.py", ["--in", rescue_candidates_path, "--variants-dir", rescue_variants_dir,
                                                         "--mode", "poster-type",
                                                         "--scores-out", rescue_scores_path, "--swaps-out", rescue_swaps_path])
    else:
        log.info("--assemble-only: skipping 02-11 (+ gate 4 rescue), reading their outputs directly")

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

    if Path(isadult_path).exists():
        with open(isadult_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["id"] in catalog and r.get("is_adult") == "1":
                    excluded[r["id"]] = f"isadult:{r.get('reason', '')}"

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

    # unresolved title mismatches: a 06 "mismatch" verdict is only kept if the
    # poster's OCR'd text (raw or translated) overlaps the catalog title or one
    # of 05's alt titles above threshold -- otherwise we can't confirm the
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

    # unresolved not-a-poster verdicts: a 04 is_movie_poster=False verdict is
    # rescued (kept) only if the alternate-poster gates found at least one
    # other TMDB image for that id that scores is_movie_poster=True; no
    # alternates found at all, or none that pass, both exclude -- see the
    # module docstring and docs/RESULTS.md, "Gate 4's alternate-poster
    # rescue."
    rescued: set[str] = set()
    if Path(rescue_swaps_path).exists():
        with open(rescue_swaps_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["id"] in catalog and r.get("propose") == "1":
                    rescued.add(r["id"])

    if Path(poster_type_path).exists():
        with open(poster_type_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                mid = r["id"]
                if (mid not in catalog or r.get("error") or r.get("is_movie_poster") != "False"
                        or mid in excluded or mid in rescued):
                    continue
                excluded[mid] = f"unresolved_not_a_poster:{r.get('method', '')}"

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
