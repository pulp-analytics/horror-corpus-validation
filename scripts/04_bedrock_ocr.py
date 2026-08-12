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
  python3 04_bedrock_ocr.py --in data/sample_input/sample_100_ids.csv
  python3 04_bedrock_ocr.py --model us.amazon.nova-lite-v1:0 --in ...
  python3 04_bedrock_ocr.py --model us.anthropic.claude-sonnet-4-5-v1:0 --in ...
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

log = get_logger("bedrock_ocr")
DEFAULT_MODEL_ID = "us.amazon.nova-pro-v1:0"
MAX_SIDE = 1200
TMDB_IMG = "https://image.tmdb.org/t/p/w780"

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/sample_input/sample_100_ids.csv")
    ap.add_argument("--out", default="data/sample_output/vision_title_check.csv")
    ap.add_argument("--model", default=DEFAULT_MODEL_ID,
                     help="any Bedrock model id that supports Converse + image input, e.g. "
                          "us.amazon.nova-pro-v1:0 (default), us.amazon.nova-lite-v1:0, "
                          "us.anthropic.claude-sonnet-4-5-v1:0")
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    bedrock = get_client("bedrock-runtime")
    session = requests.Session()
    log.info(f"using model: {args.model}")

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "title", "model", "text_you_read", "verdict", "reason", "error"]

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, row in enumerate(rows, 1):
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
                log.info(f"{i}/{len(rows)}")
            time.sleep(args.delay)

    log.info(f"wrote {out_path}")


if __name__ == "__main__":
    main()
