#!/usr/bin/env python3
"""Translate non-English poster text to English via Amazon Translate, then
re-score overlap against the catalog title.

Only calls Translate when it's actually needed (non-English text, weak local
overlap already) — see utils/constants.py for the exact thresholds
(TRANSLATE_BELOW, TRANSLATE_MIN_CHARS), which matter: in our real run only
~3,700 of ~65,000 posters needed a Translate call, out of ~5,500 that
technically qualified — the gap turned out to be an incomplete prior run
that was never resumed, not a real gap in the logic (this script now skips
ids already in --out on re-run, so that gap shouldn't recur).

  export AWS_PROFILE=your-translate-profile
  python3 06_translate_titles.py --in data/sample_output/language_detection.csv

Resumable: re-running with the same --out skips ids already processed.

Shardable: --shard-index/--shard-count split --in's rows by position, for
running N copies of this script in parallel (e.g. an AWS Batch array job)
each covering a disjoint slice. Each shard needs its own --out to merge
afterward.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_client
from utils.constants import TRANSLATE_BELOW, TRANSLATE_MIN_CHARS
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append, shard_rows
from utils.text_match import title_overlap_score

log = get_logger("translate_titles")


def translate_to_en(client, text: str) -> str:
    resp = client.translate_text(Text=text, SourceLanguageCode="auto", TargetLanguageCode="en")
    return (resp.get("TranslatedText") or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_output/language_detection.csv")
    ap.add_argument("--titles", default="data/sample_input/sample_100_ids.csv", help="csv with id,title for the overlap check")
    ap.add_argument("--out", default="data/sample_output/translated_titles.csv")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1, help="split --in across N parallel shards (default 1: no sharding)")
    args = ap.parse_args()

    translate = get_client("translate")

    with open(args.in_path, newline="", encoding="utf-8") as f:
        lang_rows_list = shard_rows(list(csv.DictReader(f)), args.shard_index, args.shard_count)
    lang_rows = {r["id"]: r for r in lang_rows_list}
    with open(args.titles, newline="", encoding="utf-8") as f:
        titles = {r["id"]: r.get("title", "") for r in csv.DictReader(f)}

    out_path = Path(args.out)
    fields = ["id", "lang_code", "text", "translated", "overlap_before", "overlap_after"]

    done = load_done_ids(out_path)
    todo_ids = [mid for mid in lang_rows if mid not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo_ids)} remaining")

    n_translated = 0
    f, w = open_for_append(out_path, fields)
    try:
        for i, mid in enumerate(todo_ids, 1):
            row = lang_rows[mid]
            title = titles.get(mid, "")
            text = row.get("text", "")
            lang = row.get("lang_code", "")
            overlap_before = title_overlap_score(text, title)

            translated, overlap_after = "", overlap_before
            needs_translate = lang and lang != "en" and len(text) >= TRANSLATE_MIN_CHARS and overlap_before < TRANSLATE_BELOW
            if needs_translate:
                translated = translate_to_en(translate, text)
                overlap_after = title_overlap_score(translated, title)
                n_translated += 1
                time.sleep(0.1)

            w.writerow({"id": mid, "lang_code": lang, "text": text, "translated": translated,
                        "overlap_before": overlap_before, "overlap_after": overlap_after})
            if i % 25 == 0:
                log.info(f"{i}/{len(todo_ids)} (translated so far: {n_translated})")
    finally:
        f.close()

    log.info(f"wrote {out_path} ({n_translated} translated this run)")


if __name__ == "__main__":
    main()
