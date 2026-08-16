#!/usr/bin/env python3
"""Score alternate poster candidates (from 11_find_alternate_posters.py)
against the catalog title via Bedrock/Nova Pro OCR, and propose swapping
the primary poster when a variant reads noticeably better -- gate 8-9's
second half.

The real project's own version of this gate, score_multi_poster_
variants_ocr.py, uses Amazon Rekognition's DetectText, not Bedrock --
confirmed by matching its output counts (806 rows / 262 candidates / 78
proposed swaps) byte-for-byte against the real data/qa/multi_poster_
variant_ocr_scores.csv and _swaps.csv. This script instead sources the
OCR read from Bedrock/Nova Pro (reusing 04_bedrock_ocr.py's
check_poster()), since that's what this repo's own gate 5 already uses
and no Rekognition-specific alternate-poster-scoring script could be
located as of this port (2026-08-16) -- ask before trusting this as a
literal reproduction of the real project's exact historical 806/262/78
run; it reproduces the real *decision logic* (same thresholds, same
title_overlap_score/title_fuzzy_score), against a different OCR source.

Decision rule (ported as-is from the real script):
  propose a swap if
    best_overlap >= --min-best AND (best_overlap - current_overlap) >= --min-gain
  OR
    best_fuzzy >= --min-fuzzy-best AND (best_fuzzy - current_fuzzy) >= --min-gain AND best_overlap >= 0.2
  OR
    current_overlap < 0.15 AND best_overlap >= --min-best

  export AWS_PROFILE=your-bedrock-profile
  python3 12_score_alternate_posters.py --in data/sample_output/vision_title_check.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_client
from utils.logging_setup import get_logger
from utils.resumable import write_csv_rows
from utils.text_match import title_fuzzy_score, title_overlap_score

# reuse 04_bedrock_ocr.py's resize_jpeg + PROMPT + DEFAULT_MODEL_ID rather
# than duplicating them -- imported by file path since a leading digit
# makes "04_bedrock_ocr" an invalid module name.
import importlib.util
_spec = importlib.util.spec_from_file_location("bedrock_ocr", Path(__file__).parent / "04_bedrock_ocr.py")
_bedrock_ocr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bedrock_ocr)

log = get_logger("score_alternate_posters")
TMDB_IMG = "https://image.tmdb.org/t/p/w500"

SCORE_FIELDS = ["id", "title", "original_title", "source", "file_path", "ocr_chars",
                "overlap_title", "overlap_original", "overlap_max",
                "fuzzy_title", "fuzzy_original", "fuzzy_max", "error"]
SWAP_FIELDS = ["id", "title", "current_file_path", "current_overlap", "current_fuzzy",
               "best_file_path", "best_overlap", "best_fuzzy", "gain_overlap", "gain_fuzzy",
               "n_variants", "propose", "reason"]


def ocr_text_via_bedrock(bedrock, image_bytes: bytes, catalog_title: str, model_id: str) -> str:
    """Same Converse call as check_poster(), but takes raw bytes directly
    (variants are on local disk already, no per-candidate TMDB re-fetch
    needed) and returns just the read text -- this gate only needs
    text_you_read, not the match/mismatch verdict check_poster() also
    computes against a single fixed title."""
    resized = _bedrock_ocr.resize_jpeg(image_bytes)
    body = {
        "messages": [{"role": "user", "content": [
            {"image": {"format": "jpeg", "source": {"bytes": resized}}},
            {"text": _bedrock_ocr.PROMPT.format(catalog_title=catalog_title)},
        ]}],
        "inferenceConfig": {"maxTokens": 300, "temperature": 0},
    }
    result = bedrock.converse(modelId=model_id, messages=body["messages"], inferenceConfig=body["inferenceConfig"])
    text = result["output"]["message"]["content"][0]["text"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text).get("text_you_read", "")


def score_text(text: str, title: str, original_title: str) -> dict:
    o1 = title_overlap_score(text, title) if text and title else 0.0
    o2 = title_overlap_score(text, original_title) if text and original_title else 0.0
    f1 = title_fuzzy_score(text, title) if text and title else 0.0
    f2 = title_fuzzy_score(text, original_title) if text and original_title else 0.0
    return {"overlap_title": o1, "overlap_original": o2, "overlap_max": max(o1, o2),
            "fuzzy_title": f1, "fuzzy_original": f2, "fuzzy_max": max(f1, f2), "ocr_chars": len(text or "")}


def propose_swap(current: dict | None, variants: list[dict], min_best: float, min_gain: float,
                  min_fuzzy_best: float) -> dict:
    """Pure function: the real project's exact 3-rule decision, given a
    current (possibly None -- no primary was scored) and a list of scored
    variant dicts, each with overlap_max/fuzzy_max/ocr_chars."""
    cur_o = float(current["overlap_max"]) if current else 0.0
    cur_f = float(current["fuzzy_max"]) if current else 0.0
    best = max(variants, key=lambda r: (r["overlap_max"], r["fuzzy_max"], r["ocr_chars"]))
    best_o, best_f = best["overlap_max"], best["fuzzy_max"]
    gain_o, gain_f = best_o - cur_o, best_f - cur_f

    propose, reason = 0, "no"
    if best_o >= min_best and gain_o >= min_gain:
        propose, reason = 1, "overlap_gain"
    elif best_f >= min_fuzzy_best and gain_f >= min_gain and best_o >= 0.2:
        propose, reason = 1, "fuzzy_gain"
    elif cur_o < 0.15 and best_o >= min_best:
        propose, reason = 1, "current_near_zero"

    return {"current_overlap": cur_o, "current_fuzzy": cur_f, "best": best,
            "best_overlap": best_o, "best_fuzzy": best_f,
            "gain_overlap": round(gain_o, 4), "gain_fuzzy": round(gain_f, 4),
            "propose": propose, "reason": reason}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_output/vision_title_check.csv")
    ap.add_argument("--variants-dir", default="data/posters_multi")
    ap.add_argument("--scores-out", default="data/sample_output/alternate_poster_scores.csv")
    ap.add_argument("--swaps-out", default="data/sample_output/alternate_poster_swaps.csv")
    ap.add_argument("--model", default=_bedrock_ocr.DEFAULT_MODEL_ID)
    ap.add_argument("--min-best", type=float, default=0.40)
    ap.add_argument("--min-gain", type=float, default=0.25)
    ap.add_argument("--min-fuzzy-best", type=float, default=0.55)
    args = ap.parse_args()

    bedrock = get_client("bedrock-runtime")
    session = requests.Session()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    variants_dir = Path(args.variants_dir)
    score_rows, swap_rows = [], []

    for i, row in enumerate(rows, 1):
        pid, title = row["id"], row.get("title", "")
        original_title = row.get("original_title", "") or title
        movie_dir = variants_dir / pid
        variant_files = sorted(movie_dir.glob("*.jpg")) if movie_dir.is_dir() else []
        if not variant_files:
            continue

        current = None
        if row.get("poster_path"):
            resp = session.get(f"{TMDB_IMG}{row['poster_path']}", timeout=20)
            if resp.status_code == 200:
                try:
                    text = ocr_text_via_bedrock(bedrock, resp.content, title, args.model)
                    current = {"id": pid, "title": title, "original_title": original_title,
                               "source": "primary", "file_path": row["poster_path"], "error": "",
                               **score_text(text, title, original_title)}
                except Exception as e:
                    current = {"id": pid, "title": title, "original_title": original_title,
                               "source": "primary", "file_path": row["poster_path"],
                               "overlap_max": 0.0, "fuzzy_max": 0.0, "ocr_chars": 0, "error": str(e)[:200]}
                score_rows.append(current)

        variants = []
        for vf in variant_files:
            try:
                text = ocr_text_via_bedrock(bedrock, vf.read_bytes(), title, args.model)
                v = {"id": pid, "title": title, "original_title": original_title, "source": "variant",
                     "file_path": f"/{vf.name}", "error": "", **score_text(text, title, original_title)}
            except Exception as e:
                v = {"id": pid, "title": title, "original_title": original_title, "source": "variant",
                     "file_path": f"/{vf.name}", "overlap_max": 0.0, "fuzzy_max": 0.0, "ocr_chars": 0,
                     "error": str(e)[:200]}
            score_rows.append(v)
            if not v["error"]:
                variants.append(v)

        if variants:
            decision = propose_swap(current, variants, args.min_best, args.min_gain, args.min_fuzzy_best)
            swap_rows.append({
                "id": pid, "title": title,
                "current_file_path": (current or {}).get("file_path", ""),
                "current_overlap": decision["current_overlap"], "current_fuzzy": decision["current_fuzzy"],
                "best_file_path": decision["best"]["file_path"],
                "best_overlap": decision["best_overlap"], "best_fuzzy": decision["best_fuzzy"],
                "gain_overlap": decision["gain_overlap"], "gain_fuzzy": decision["gain_fuzzy"],
                "n_variants": len(variants), "propose": decision["propose"], "reason": decision["reason"],
            })

        if i % 10 == 0 or i == len(rows):
            log.info(f"{i}/{len(rows)}")

    write_csv_rows(args.scores_out, score_rows)
    write_csv_rows(args.swaps_out, swap_rows)
    n_proposed = sum(1 for r in swap_rows if r["propose"] == 1)
    log.info(f"wrote {args.scores_out} ({len(score_rows)} rows), {args.swaps_out} "
             f"({len(swap_rows)} rows, {n_proposed} proposed swaps)")


if __name__ == "__main__":
    main()
