#!/usr/bin/env python3
"""Poster-type gate: filters out candidates whose `poster_path` resolves
(gate 3 already confirmed that) but doesn't actually depict real movie
poster art -- a film still, a generic/stock photo, a blank or placeholder
image, or fan art. Gate 3 only checks the image is *reachable*; nothing
before this gate checks it's actually poster art. See docs/RESULTS.md,
"Poster-type human review" and "Poster-type's missing LLM leg, closed".

Two-stage design, following exactly what was validated (not guessed):

  1. Deterministic pre-filter (Rekognition DetectText): if any real text
     line is detected on the poster, it's assumed to be real poster art
     and Nova is never called. This is safe specifically because the
     2,528-row human ground truth this gate is scored against was built
     ONLY from zero-OCR-text candidates (real posters with visible text
     were never part of the "might not be a poster" question at all) --
     see docs/RESULTS.md's "Poster-type human review" section. Also the
     cheap path: most of the corpus has real text and never needs the
     LLM call this gate would otherwise make for every row.

  2. LLM leg (Nova, zero-OCR candidates only): a direct question -- "is
     this real poster key art or not" -- not a repurposed classifier.
     Live-validated 2026-08-17 against all 2,527 scoreable rows of
     data/ground_truth/poster_type_human_labels.csv: 91.6% accuracy,
     85.5% precision, 82.4% recall. The only other vision signal tried
     for this (CLIP's `painted` classifier, cross-referenced in
     docs/RESULTS.md) had no discriminative value -- it was repurposed
     from an unrelated task, not a direct question, which is exactly
     the gap this gate closes.

Deliberately NOT included in this gate: checking every alternate TMDB
poster variant before a final reject (proven live to rescue 8.9% of
zero-OCR candidates, docs/RESULTS.md "Before filtering... check every
other poster TMDB has") -- that's a separate, heavier TMDB-multi-image
fetch belonging to the alternate-poster domain (gates 12-13), not this
gate's single-image check. Recommended as a follow-up before permanently
excluding anything this gate rejects.

  TMDB_API_KEY=... AWS_PROFILE=... python3 04_filter_poster_type.py \\
      --in data/sample_output/vision_title_check.csv
  python3 04_filter_poster_type.py --validate

--validate scores this gate's live two-stage logic against the real
2,528-row human-reviewed ground truth and reports accuracy/precision/
recall, the same pattern as 06_bedrock_ocr.py's --validate.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from botocore.exceptions import ClientError
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_client
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append, shard_rows, write_csv_rows
from utils.tmdb_client import IMAGE_BASE_URL

log = get_logger("filter_poster_type")

DEFAULT_MODEL_ID = "us.amazon.nova-pro-v1:0"
TMDB_IMG = f"{IMAGE_BASE_URL}w342"
MAX_SIDE = 1200

POSTER_TYPE_PROMPT = """You are shown a single image on file in a movie database as a
film's poster.

Answer with a single JSON object, no other text:
{"is_movie_poster": true|false, "confidence": "high"|"medium"|"low"}

is_movie_poster: true if this image is real theatrical/promotional poster
key art for a film -- a designed image meant to advertise the movie
(illustrated or photographic composition with clear poster-style visual
design intent). It can have a title/tagline/credits block, or be
textless/minimalist/international-style with no text at all -- either is
still a real poster.

false if it is NOT real poster art: a plain film still or production
photograph, an unrelated stock/generic image, a blank/solid-color or
placeholder image, a logo-only card, fan art clearly not official key
art, or any other image that isn't actually designed as poster
advertising for this film."""

OUT_FIELDS = ["id", "title", "poster_path", "has_ocr_text", "method",
              "nova_confidence", "is_movie_poster", "error"]

_write_lock = threading.Lock()


def resize_jpeg(raw: bytes, max_side: int = MAX_SIDE) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def has_real_text(rekognition, img_bytes: bytes, attempts: int = 5) -> bool:
    last_exc = None
    for attempt in range(attempts):
        try:
            resp = rekognition.detect_text(Image={"Bytes": img_bytes})
            break
        except ClientError as e:
            last_exc = e
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ThrottlingException", "ProvisionedThroughputExceededException"):
                time.sleep(min(16, 1.2 * (2 ** attempt)))
                continue
            raise
    else:
        raise last_exc  # type: ignore[misc]
    lines = [d for d in (resp.get("TextDetections") or [])
             if d.get("Type") == "LINE" and (d.get("DetectedText") or "").strip()]
    return len(lines) > 0


def classify_poster_type(bedrock, img_bytes: bytes, model_id: str) -> dict:
    body = {
        "messages": [{
            "role": "user",
            "content": [
                {"image": {"format": "jpeg", "source": {"bytes": img_bytes}}},
                {"text": POSTER_TYPE_PROMPT},
            ],
        }],
        "inferenceConfig": {"maxTokens": 100, "temperature": 0},
    }
    result = bedrock.converse(modelId=model_id, messages=body["messages"], inferenceConfig=body["inferenceConfig"])
    text = result["output"]["message"]["content"][0]["text"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0)) if m else {}
    return {
        "is_movie_poster": bool(data.get("is_movie_poster", True)),
        "confidence": str(data.get("confidence", "")),
    }


def process_one(row: dict, bedrock, rekognition, session: requests.Session, model_id: str) -> dict:
    pid, title, poster_path = row["id"], row.get("title", ""), row.get("poster_path", "")
    out = {"id": pid, "title": title, "poster_path": poster_path,
           "has_ocr_text": "", "method": "", "nova_confidence": "", "is_movie_poster": "", "error": ""}
    if not poster_path:
        out["error"] = "no poster_path"
        return out
    try:
        resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=15)
        resp.raise_for_status()
        img_bytes = resize_jpeg(resp.content)

        text_present = has_real_text(rekognition, img_bytes)
        out["has_ocr_text"] = text_present

        if text_present:
            out["method"] = "ocr_text_present"
            out["is_movie_poster"] = True
        else:
            out["method"] = "nova_zero_ocr"
            result = classify_poster_type(bedrock, img_bytes, model_id)
            out["is_movie_poster"] = result["is_movie_poster"]
            out["nova_confidence"] = result["confidence"]
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def compute_validate_metrics(results: list[dict]) -> dict:
    """Pure function: accuracy/precision/recall of live is_movie_poster
    against human_verdict (es_poster=True, no_es_poster=False). Rows with
    an error, or a human_verdict outside {es_poster, no_es_poster}
    (no_seguro/blank), are excluded."""
    scored = [r for r in results if not r.get("error") and r["human_verdict"] in ("es_poster", "no_es_poster")]
    n = len(scored)
    tp = sum(1 for r in scored if r["is_movie_poster"] == "True" and r["human_verdict"] == "es_poster")
    fp = sum(1 for r in scored if r["is_movie_poster"] == "True" and r["human_verdict"] == "no_es_poster")
    fn = sum(1 for r in scored if r["is_movie_poster"] == "False" and r["human_verdict"] == "es_poster")
    tn = sum(1 for r in scored if r["is_movie_poster"] == "False" and r["human_verdict"] == "no_es_poster")
    correct = tp + tn
    return {
        "n_scored": n, "n_errored": len(results) - n,
        "accuracy": correct / n if n else None,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "recall": tp / (tp + fn) if (tp + fn) else None,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def print_validate_report(results: list[dict]) -> None:
    m = compute_validate_metrics(results)
    if m["n_errored"]:
        log.info(f"{m['n_errored']} row(s) errored during live scoring -- excluded from metrics")
    n = m["n_scored"]
    if n == 0:
        log.info("nothing scored -- can't report metrics")
        return

    def pct(v):
        return f"{v*100:.1f}%" if v is not None else "n/a"

    print(f"\nn={n}  accuracy={pct(m['accuracy'])}  precision={pct(m['precision'])}  recall={pct(m['recall'])}")
    print(f"TP={m['tp']}  FP={m['fp']}  FN={m['fn']}  TN={m['tn']}")


def run_validate(bedrock, rekognition, session: requests.Session, args) -> None:
    gt_path = Path(args.ground_truth)
    with gt_path.open(newline="", encoding="utf-8") as f:
        gt_rows = [r for r in csv.DictReader(f) if r.get("human_verdict") in ("es_poster", "no_es_poster")]
    log.info(f"{len(gt_rows)} human-labeled rows to score live against {args.model}")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process_one, row, bedrock, rekognition, requests.Session(), args.model): row for row in gt_rows}
        n_done = 0
        for fut in as_completed(futs):
            row = futs[fut]
            out = fut.result()
            out["human_verdict"] = row["human_verdict"]
            results.append(out)
            n_done += 1
            if n_done % 100 == 0 or n_done == len(gt_rows):
                log.info(f"{n_done}/{len(gt_rows)}")

    validate_out = Path(args.validate_out)
    write_csv_rows(validate_out, results)
    log.info(f"wrote {validate_out} ({len(results)} rows)")
    print_validate_report(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/poster_type_filter.csv")
    ap.add_argument("--rescue-out", default=None,
                     help="if given, write just the is_movie_poster=False rows (id/title/poster_path) to this "
                          "path -- gate 13/14's rescue --in, so they don't spend real budget on candidates "
                          "that don't need rescuing. See 12_validate_corpus.py and docs/RESULTS.md, 'Gate 4's "
                          "alternate-poster rescue.'")
    ap.add_argument("--model", default=DEFAULT_MODEL_ID)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--retry-errors", action="store_true")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1, help="split --in across N parallel shards (default 1: no sharding)")
    ap.add_argument("--validate", action="store_true",
                     help="score this gate's live two-stage logic against --ground-truth "
                          "and report accuracy/precision/recall instead of the normal --in run")
    ap.add_argument("--ground-truth", default="data/ground_truth/poster_type_human_labels.csv")
    ap.add_argument("--validate-out", default="data/ground_truth/poster_type_filter_validate_results.csv")
    args = ap.parse_args()

    bedrock = get_client("bedrock-runtime")
    rekognition = get_client("rekognition")
    session = requests.Session()
    log.info(f"using model: {args.model}")

    if args.validate:
        run_validate(bedrock, rekognition, session, args)
        return

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = shard_rows(rows, args.shard_index, args.shard_count)

    out_path = Path(args.out)
    done = load_done_ids(out_path, args.retry_errors)
    todo = [row for row in rows if row["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo)} remaining")

    f, w = open_for_append(out_path, OUT_FIELDS)
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(process_one, row, bedrock, rekognition, requests.Session(), args.model): row for row in todo}
            n_done = 0
            for fut in as_completed(futs):
                out = fut.result()
                with _write_lock:
                    w.writerow(out)
                    f.flush()
                n_done += 1
                if n_done % 25 == 0 or n_done == len(todo):
                    log.info(f"{n_done}/{len(todo)}")
    finally:
        f.close()

    log.info(f"wrote {out_path}")

    if args.rescue_out:
        # Full, resumed --out (not just this run's todo) -- a rescue list
        # should reflect every is_movie_poster=False verdict gate 4 has
        # ever reached, not just this invocation's slice.
        with out_path.open(newline="", encoding="utf-8") as f:
            rescue_rows = [r for r in csv.DictReader(f)
                           if not r.get("error") and r.get("is_movie_poster") == "False"]
        rescue_path = Path(args.rescue_out)
        rescue_path.parent.mkdir(parents=True, exist_ok=True)
        with rescue_path.open("w", newline="", encoding="utf-8") as f:
            w2 = csv.DictWriter(f, fieldnames=["id", "title", "poster_path"])
            w2.writeheader()
            for r in rescue_rows:
                w2.writerow({"id": r["id"], "title": r.get("title", ""), "poster_path": r.get("poster_path", "")})
        log.info(f"wrote {rescue_path}: {len(rescue_rows)} is_movie_poster=False id(s) to rescue")


if __name__ == "__main__":
    main()
