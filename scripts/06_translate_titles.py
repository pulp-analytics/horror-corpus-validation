#!/usr/bin/env python3
"""Translate non-English poster text to English via Amazon Translate, then
re-score overlap against the catalog title.

Only calls Translate when it's actually needed (non-English text, weak local
overlap already) — see utils/constants.py for the exact thresholds
(TRANSLATE_BELOW, TRANSLATE_MIN_CHARS), which matter: in our real run only
~3,700 of ~65,000 posters needed a Translate call, out of ~5,500 that
technically qualified — the gap turned out to be an incomplete prior run,
not a real gap in the logic. Re-run with the same thresholds to fill gaps
without re-processing everything (idempotent by design).

  export AWS_PROFILE=your-translate-profile
  python3 06_translate_titles.py --in data/sample_output/language_detection.csv
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
    args = ap.parse_args()

    translate = get_client("translate")

    with open(args.in_path, newline="", encoding="utf-8") as f:
        lang_rows = {r["id"]: r for r in csv.DictReader(f)}
    with open(args.titles, newline="", encoding="utf-8") as f:
        titles = {r["id"]: r.get("title", "") for r in csv.DictReader(f)}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "lang_code", "text", "translated", "overlap_before", "overlap_after"]

    n_translated = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, (mid, row) in enumerate(lang_rows.items(), 1):
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
                log.info(f"{i}/{len(lang_rows)} (translated so far: {n_translated})")

    log.info(f"wrote {out_path} ({n_translated} translated)")


if __name__ == "__main__":
    main()
