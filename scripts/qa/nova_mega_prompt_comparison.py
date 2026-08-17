#!/usr/bin/env python3
"""One-off experiment (not a re-ported gate): run the real project's
nova_poster_enrich.py ENRICH_PROMPT (one Nova call asking for title text,
credits, mood, fear labels, weapon/monster/person/animal, blood_gore,
violence, sexual_content, sensitive tags, and a description all at once)
against the same 672 posters this repo already scored with ISOLATED
per-task prompts (04_bedrock_ocr.py for title text only,
13_content_moderation.py for moderation scores only) -- same model
(us.amazon.nova-pro-v1:0), same images, only the prompt structure differs.

Answers a real design question: does asking Nova one narrow question per
call actually produce different (better/worse) results than asking it
everything at once?

  AWS_PROFILE=... python3 scripts/qa/nova_mega_prompt_comparison.py \
      --in data/ground_truth/content_moderation_es_poster.csv \
      --out data/qa/nova_mega_prompt_comparison.csv --workers 20
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.aws_config import get_client
from utils.logging_setup import get_logger
from utils.tmdb_client import IMAGE_BASE_URL

log = get_logger("nova_mega_prompt")

MODEL_ID = "us.amazon.nova-pro-v1:0"
TMDB_IMG = f"{IMAGE_BASE_URL}w780"
MAX_SIDE = 1280

# Copied verbatim from the real project's nova_poster_enrich.py (2026-08-16).
ENRICH_PROMPT = """You analyze a movie poster image. Return ONLY valid JSON (no markdown) with this schema:
{
  "title_text": "main title as printed on the poster (empty if none)",
  "credits_text": "tagline, cast, director, studio, billing block text (empty if none)",
  "languages": ["en"],
  "other_text": "any other visible text not in title/credits",
  "mood": ["up to 5 short mood/atmosphere tags, e.g. dread, camp, gothic, erotic, surreal"],
  "fear_labels": [{"name":"label","conf":0.0}],
  "weapon": 0.0,
  "monster": 0.0,
  "person": 0.0,
  "animal": 0.0,
  "blood_gore": 0.0,
  "violence": 0.0,
  "sexual_content": 0.0,
  "sensitive": ["optional tags: violence, gore, nudity, sexual, occult, self-harm, none"],
  "moderation_notes": "one short sentence on sensitive content, or empty",
  "description": "1-2 sentence neutral visual description for search/embeddings"
}

Rules:
- fear_labels: up to 12 visual concepts useful for horror analysis (weapon, knife, gun, monster, creature, ghost, skull, blood, fire, water, silhouette, face, crowd, house, forest, vehicle, text-heavy, etc.). conf in 0..1.
- weapon/monster/person/animal/blood_gore/violence/sexual_content: likelihood 0..1 from the poster artwork.
- languages: ISO-like codes inferred from visible text (en, es, ja, ...). Use [] if no text.
- Keep description factual and concise (<= 45 words). No spoilers beyond what the poster shows.
"""

OUT_FIELDS = ["id", "title", "mega_title_text", "mega_blood_gore", "mega_violence",
              "mega_sexual_content", "mega_sensitive", "error"]

_lock = threading.Lock()


def resize_jpeg(raw: bytes, max_side: int = MAX_SIDE) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _score(val, default: float = 0.0) -> float:
    try:
        x = float(val)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, x)), 4)


def process_one(row: dict, bedrock, session: requests.Session) -> dict:
    pid, title, poster_path = row["id"], row.get("title", ""), row.get("poster_path", "")
    out = {"id": pid, "title": title, "error": ""}
    try:
        resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=15)
        resp.raise_for_status()
        img_bytes = resize_jpeg(resp.content)

        result = bedrock.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [
                {"image": {"format": "jpeg", "source": {"bytes": img_bytes}}},
                {"text": ENRICH_PROMPT},
            ]}],
            inferenceConfig={"maxTokens": 600, "temperature": 0},
        )
        text = result["output"]["message"]["content"][0]["text"]
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(text)

        out["mega_title_text"] = str(data.get("title_text") or "").strip()
        out["mega_blood_gore"] = _score(data.get("blood_gore"))
        out["mega_violence"] = _score(data.get("violence"))
        out["mega_sexual_content"] = _score(data.get("sexual_content"))
        out["mega_sensitive"] = "|".join(str(t) for t in (data.get("sensitive") or []))
    except Exception as e:
        out["error"] = str(e)[:300]
        for f in OUT_FIELDS:
            out.setdefault(f, "")

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", default="data/qa/nova_mega_prompt_comparison.csv")
    ap.add_argument("--workers", type=int, default=20)
    args = ap.parse_args()

    bedrock = get_client("bedrock-runtime")

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        with out_path.open(newline="", encoding="utf-8") as f:
            done = {r["id"] for r in csv.DictReader(f) if r.get("id")}
    todo = [r for r in rows if r["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo)} remaining")

    file_exists = out_path.exists() and out_path.stat().st_size > 0
    out_f = out_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_f, fieldnames=OUT_FIELDS)
    if not file_exists:
        writer.writeheader()
        out_f.flush()

    n_done, n_err = 0, 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_one, row, bedrock, requests.Session()): row for row in todo}
            for fut in as_completed(futures):
                result = fut.result()
                with _lock:
                    writer.writerow(result)
                    out_f.flush()
                    n_done += 1
                    if result.get("error"):
                        n_err += 1
                    if n_done % 25 == 0 or n_done == len(todo):
                        log.info(f"{n_done}/{len(todo)} (errors: {n_err})")
    finally:
        out_f.close()

    log.info(f"wrote {out_path} ({n_done} this run, {n_err} errors)")


if __name__ == "__main__":
    main()
