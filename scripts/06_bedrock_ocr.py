#!/usr/bin/env python3
"""Vision-model QA of a poster's title text, via Amazon Bedrock.

Downloads each poster directly from TMDB's image CDN (no local poster cache
needed), asks a vision-capable Bedrock model what title text it actually
sees, and judges whether that matches the catalog title. This is the check
that caught cases where a "different title on the poster" was actually a
legitimate foreign/reissue title rather than a wrong poster — see
docs/RESULTS.md.

Defaults to Nova Pro (what the full-corpus run in docs/RESULTS.md used),
but --model accepts any Bedrock model id that supports the Converse API's
image input — swap in Nova Lite (~18x cheaper, less precise), a Claude
model, or anything else Bedrock exposes, without touching the code.

  export AWS_PROFILE=your-bedrock-profile
  python3 06_bedrock_ocr.py --in data/sample_input/sample_100_ids.csv
  python3 06_bedrock_ocr.py --model us.amazon.nova-lite-v1:0 --in ...
  python3 06_bedrock_ocr.py --model us.anthropic.claude-sonnet-4-5-v1:0 --in ...

Resumable: re-running with the same --out skips ids already in that file
(including ones that errored last time -- add --retry-errors to redo just
those) and appends new results, instead of re-spending Bedrock calls on
work already done.

Depends on 03_verify_poster_exists.py having already run: --verified points
at its output, and any id it marked unverified (empty poster_path, or a 404
on the TMDB image URL) is skipped here too, instead of wasting a download
attempt on a poster we already know is unreachable. If --verified doesn't
exist (e.g. you're running this script standalone), falls back to just
checking poster_path is non-empty.

Shardable: --shard-index/--shard-count split --in's rows by position, for
running N copies of this script in parallel (e.g. an AWS Batch array job)
each covering a disjoint slice -- the highest-value script to shard, since
it's a Bedrock call per row and the slowest/most numerous step in the full
corpus. Each shard needs its own --out to merge afterward.

  python3 06_bedrock_ocr.py --validate

--validate scores --model live against data/ground_truth/
bedrock_ocr_ground_truth_human_labels.csv -- 100 real posters a human
reviewed blind (see docs/MODELS.md, "Building a human ground-truth set"),
excluding rows marked "unjudgeable" (a script the reviewer couldn't read).
Reports overall accuracy plus per-class precision/recall/support, and
writes the raw per-row comparison to --validate-out. This makes real
Bedrock calls against ~75 posters -- a few cents, not free, and the
result reflects whatever --model serves *today*, which for a managed
model like Nova isn't guaranteed to match what produced the human labels'
original stratum (see docs/MODELS.md's note on why Bedrock can't be
pinned the way the sibling repo pins CLIP/SigLIP).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_client
from utils.logging_setup import get_logger
from utils.resumable import open_for_append, shard_rows, write_csv_rows
from utils.tmdb_client import IMAGE_BASE_URL

log = get_logger("bedrock_ocr")
DEFAULT_MODEL_ID = "us.amazon.nova-pro-v1:0"
MAX_SIDE = 1200
TMDB_IMG = f"{IMAGE_BASE_URL}w780"

PROMPT = """Read any title text visible on this movie poster (the main film title,
as printed on the artwork -- ignore tagline/credits/small print unless no title exists).

The catalog lists this film as: "{catalog_title}"

Return ONLY valid JSON (no markdown):
{{
  "text_you_read": "the title text you actually see on the poster, or empty if none",
  "verdict": "match" | "mismatch" | "no_title_on_poster",
  "reason": "one short sentence"
}}
"""


def resize_jpeg(raw: bytes, max_side: int = MAX_SIDE) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def check_poster(bedrock, session: requests.Session, poster_path: str, catalog_title: str, model_id: str) -> dict:
    resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=15)
    resp.raise_for_status()
    img_bytes = resize_jpeg(resp.content)

    body = {
        "messages": [{
            "role": "user",
            "content": [
                {"image": {"format": "jpeg", "source": {"bytes": img_bytes}}},
                {"text": PROMPT.format(catalog_title=catalog_title)},
            ],
        }],
        "inferenceConfig": {"maxTokens": 300, "temperature": 0},
    }
    # Any Bedrock model id that supports Converse + image input works here --
    # not tied to Nova specifically.
    result = bedrock.converse(modelId=model_id, messages=body["messages"], inferenceConfig=body["inferenceConfig"])
    text = result["output"]["message"]["content"][0]["text"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


def load_done_ids(path: Path, retry_errors: bool) -> set[str]:
    """Like utils.resumable.load_done_ids, but optionally treats rows that
    errored last time as NOT done, so a re-run retries just those."""
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


VALIDATE_CLASSES = ["match", "mismatch", "no_title_on_poster"]


def compute_validate_metrics(results: list[dict]) -> dict:
    """Pure function (no I/O) over a list of {human_verdict, live_verdict,
    error} dicts: overall accuracy plus per-class precision/recall/support.
    Rows with a non-empty `error` (a failed live call) are excluded from
    every count -- they're neither a right nor a wrong answer, just a call
    that didn't complete."""
    scored = [r for r in results if not r.get("error")]
    n = len(scored)
    correct = sum(1 for r in scored if r["human_verdict"] == r["live_verdict"])

    per_class = {}
    for c in VALIDATE_CLASSES:
        tp = sum(1 for r in scored if r["live_verdict"] == c and r["human_verdict"] == c)
        fp = sum(1 for r in scored if r["live_verdict"] == c and r["human_verdict"] != c)
        fn = sum(1 for r in scored if r["live_verdict"] != c and r["human_verdict"] == c)
        support = sum(1 for r in scored if r["human_verdict"] == c)
        per_class[c] = {
            "precision": tp / (tp + fp) if (tp + fp) else None,
            "recall": tp / (tp + fn) if (tp + fn) else None,
            "support": support,
        }

    return {
        "n_scored": n,
        "n_errored": len(results) - n,
        "accuracy": correct / n if n else None,
        "per_class": per_class,
    }


def print_validate_report(results: list[dict]) -> None:
    metrics = compute_validate_metrics(results)
    if metrics["n_errored"]:
        log.info(f"{metrics['n_errored']} row(s) errored during live scoring -- excluded from metrics:")
        for r in results:
            if r.get("error"):
                log.info(f"  {r['id']}: {r['error']}")

    n = metrics["n_scored"]
    if n == 0:
        log.info("nothing scored -- can't report metrics")
        return

    acc = metrics["accuracy"]
    print(f"\nOverall accuracy: {round(acc * n)}/{n} = {acc * 100:.1f}%\n")

    print(f'{"class":20}{"precision":>12}{"recall":>10}{"support":>10}')
    for c in VALIDATE_CLASSES:
        pc = metrics["per_class"][c]
        prec = f"{pc['precision']*100:.1f}%" if pc["precision"] is not None else "n/a"
        rec = f"{pc['recall']*100:.1f}%" if pc["recall"] is not None else "n/a"
        print(f"{c:20}{prec:>12}{rec:>10}{pc['support']:>10}")

    scored = [r for r in results if not r.get("error")]
    print("\nConfusion matrix (rows=human, cols=live):")
    print(f'{"":20}' + "".join(f"{c[:13]:>15}" for c in VALIDATE_CLASSES))
    for hc in VALIDATE_CLASSES:
        row_counts = [sum(1 for r in scored if r["human_verdict"] == hc and r["live_verdict"] == lc)
                      for lc in VALIDATE_CLASSES]
        print(f"{hc:20}" + "".join(f"{v:15}" for v in row_counts))


def run_validate(bedrock, session: requests.Session, args) -> None:
    gt_path = Path(args.ground_truth)
    with gt_path.open(newline="", encoding="utf-8") as f:
        gt_rows = list(csv.DictReader(f))

    judged = [r for r in gt_rows if r.get("human_verdict") and r["human_verdict"] != "unjudgeable"]
    log.info(f"{len(judged)}/{len(gt_rows)} ground-truth rows judged "
             f"({len(gt_rows) - len(judged)} unjudgeable, excluded) -- scoring {args.model} live")

    results = []
    for i, row in enumerate(judged, 1):
        out = {"id": row["id"], "title": row.get("title", ""), "stratum": row.get("stratum", ""),
               "human_verdict": row["human_verdict"], "live_verdict": "", "error": ""}
        try:
            live = check_poster(bedrock, session, row["poster_path"], row.get("title", ""), args.model)
            out["live_verdict"] = live.get("verdict", "")
        except Exception as e:
            out["error"] = str(e)[:200]
        results.append(out)
        if i % 10 == 0:
            log.info(f"{i}/{len(judged)}")
        time.sleep(args.delay)

    validate_out = Path(args.validate_out)
    write_csv_rows(validate_out, results)
    log.info(f"wrote {validate_out} ({len(results)} rows)")

    print_validate_report(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/vision_title_check.csv")
    ap.add_argument("--verified", default="data/sample_output/poster_verification.csv",
                     help="output of 03_verify_poster_exists.py; ids not marked verified=1 there "
                          "are skipped here without attempting a download. Pass '' to disable.")
    ap.add_argument("--model", default=DEFAULT_MODEL_ID,
                     help="any Bedrock model id that supports Converse + image input, e.g. "
                          "us.amazon.nova-pro-v1:0 (default), us.amazon.nova-lite-v1:0, "
                          "us.anthropic.claude-sonnet-4-5-v1:0")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--retry-errors", action="store_true",
                     help="on resume, redo ids that errored last time instead of skipping them")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1, help="split --in across N parallel shards (default 1: no sharding)")
    ap.add_argument("--validate", action="store_true",
                     help="score --model live against --ground-truth's human-reviewed labels "
                          "and report accuracy/precision/recall instead of the normal --in run "
                          "(makes real Bedrock calls -- see docs/MODELS.md)")
    ap.add_argument("--ground-truth", default="data/ground_truth/bedrock_ocr_ground_truth_human_labels.csv")
    ap.add_argument("--validate-out", default="data/ground_truth/bedrock_ocr_validate_results.csv")
    args = ap.parse_args()

    bedrock = get_client("bedrock-runtime")
    session = requests.Session()
    log.info(f"using model: {args.model}")

    if args.validate:
        run_validate(bedrock, session, args)
        return

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

    out_path = Path(args.out)
    fields = ["id", "title", "model", "text_you_read", "verdict", "reason", "error"]

    done = load_done_ids(out_path, args.retry_errors)
    todo = [row for row in rows if row["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo)} remaining")

    # --retry-errors appends fresh rows for retried ids rather than editing
    # in place, so a downstream reader should keep the LAST row per id.
    f, w = open_for_append(out_path, fields)
    try:
        for i, row in enumerate(todo, 1):
            out = {"id": row["id"], "title": row.get("title", ""), "model": args.model,
                   "text_you_read": "", "verdict": "", "reason": "", "error": ""}
            if not row.get("poster_path"):
                out["error"] = "no poster_path"
            else:
                try:
                    result = check_poster(bedrock, session, row["poster_path"], row.get("title", ""), args.model)
                    out.update(text_you_read=result.get("text_you_read", ""), verdict=result.get("verdict", ""), reason=result.get("reason", ""))
                except Exception as e:
                    out["error"] = str(e)[:200]
            w.writerow(out)
            if i % 10 == 0:
                log.info(f"{i}/{len(todo)}")
            time.sleep(args.delay)
    finally:
        f.close()

    log.info(f"wrote {out_path}")


if __name__ == "__main__":
    main()
