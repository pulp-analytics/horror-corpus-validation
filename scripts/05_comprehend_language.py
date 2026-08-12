#!/usr/bin/env python3
"""Detect the language of a poster's OCR'd title text via Amazon Comprehend.

This is compared against the catalog's `original_language` field — the two
often legitimately disagree (an international release poster in English for
a non-English film is normal), so this alone should never be used to flag a
poster as wrong. See docs/RESULTS.md for the real breakdown we found: ~79%
of disagreements were the expected "foreign film, English-market poster"
pattern, not errors.

  export AWS_PROFILE=your-comprehend-profile
  python3 05_comprehend_language.py --in data/sample_output/vision_title_check.csv

Resumable: re-running with the same --out skips ids already processed.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_client
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append

log = get_logger("comprehend_language")


def detect_language(comprehend, text: str) -> tuple[str, float]:
    if not text or not text.strip():
        return "", 0.0
    resp = comprehend.detect_dominant_language(Text=text[:4900])
    langs = resp.get("Languages", [])
    if not langs:
        return "", 0.0
    top = max(langs, key=lambda l: l["Score"])
    return top["LanguageCode"], round(top["Score"], 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_output/vision_title_check.csv")
    ap.add_argument("--text-col", default="text_you_read")
    ap.add_argument("--out", default="data/sample_output/language_detection.csv")
    args = ap.parse_args()

    comprehend = get_client("comprehend")

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_path = Path(args.out)
    fields = ["id", "text", "lang_code", "lang_score"]

    done = load_done_ids(out_path)
    todo = [row for row in rows if row["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo)} remaining")

    f, w = open_for_append(out_path, fields)
    try:
        for i, row in enumerate(todo, 1):
            text = row.get(args.text_col, "")
            lang_code, lang_score = detect_language(comprehend, text)
            w.writerow({"id": row["id"], "text": text, "lang_code": lang_code, "lang_score": lang_score})
            if i % 25 == 0:
                log.info(f"{i}/{len(todo)}")
            time.sleep(0.05)
    finally:
        f.close()

    log.info(f"wrote {out_path}")


if __name__ == "__main__":
    main()
