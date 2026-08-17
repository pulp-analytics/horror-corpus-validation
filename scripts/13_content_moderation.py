#!/usr/bin/env python3
"""Content moderation gate: flags posters whose gore/violence/sexual-content
severity crosses this project's own real thresholds, using two independent
signals ported faithfully from the real project's already-run scripts --
nova_poster_enrich.py (a Nova vision-LLM call scoring blood_gore/violence/
sexual_content 0-1) and rekognition_enrich.py (Amazon Rekognition's
purpose-built detect_moderation_labels API). See docs/VALIDATION_LOGIC.md.

Why two engines, not one: same reasoning behind this repo's gates 5 and
8-9 using multiple OCR engines -- a single vision model's judgment on "how
extreme is this image" shouldn't be trusted alone. Nova gives a direct
severity read from one vision-LLM call; Rekognition's moderation API is a
separately-trained, purpose-built classifier with its own label taxonomy --
an independent second opinion, not the same model asked twice.

Real thresholds, not invented ones:
  - NOVA_THRESHOLD = 0.5, from the real project's own nova_enrich_live_summary.py,
    which reports corpus-wide "how much crosses 0.5" for blood_gore/violence.
  - REK_THRESHOLD = 0.4, from the real project's own rekognition_enrich.py,
    whose decade_summary() uses exactly this cutoff to call a decade
    "flagged" for violence/gore.

One real, faithful extension beyond what the real project's script
extracted: Rekognition's detect_moderation_labels already returns
"Explicit Nudity"/"Suggestive" labels in the same API response --
rekognition_enrich.py only ever parsed Violence/Gore/Weapons out of it,
leaving nudity/suggestive sitting unread in the raw `rek_mod` string. This
extracts them the same way (same _mod_score pattern as the real script)
as rek_nudity/rek_suggestive -- the signal was already being paid for.

Deliberately narrower than nova_poster_enrich.py's real prompt: the real
call also returns title_text/credits_text/mood/fear_labels/weapon/monster/
etc in one shared pass -- this gate only asks for the moderation-relevant
fields, since OCR is already this repo's gate 4 and duplicating it here
would waste tokens on a question this gate doesn't need answered.

Model/region default to this repo's own already-verified Bedrock setup
(us.amazon.nova-pro-v1:0, us-east-1, same as gates 4/12) rather than the
real script's us.amazon.nova-2-lite-v1:0 in us-west-2 -- not verified
available in every account/region, and this repo standardized on Nova Pro
already. --model/--region override both if you want the real script's
exact model instead.

  TMDB_API_KEY=... AWS_PROFILE=... python3 13_content_moderation.py \\
      --in data/ground_truth/poster_type_sample.csv --workers 16

Resumable + concurrent: --out is the checkpoint (one row per id, written
via a lock so concurrent workers don't interleave writes), a
ThreadPoolExecutor runs --workers ids at once, each doing its own Nova +
Rekognition calls. Rekognition throttling gets the real script's own
retry/backoff treatment (exponential, up to 6 attempts) rather than
failing the row outright.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
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
from utils.resumable import load_done_ids, write_csv_rows
from utils.tmdb_client import IMAGE_BASE_URL

log = get_logger("content_moderation")

DEFAULT_MODEL_ID = "us.amazon.nova-pro-v1:0"
TMDB_IMG = f"{IMAGE_BASE_URL}w780"
MAX_SIDE = 1200

NOVA_THRESHOLD = 0.5
REK_THRESHOLD = 0.4

MODERATION_PROMPT = """You analyze a movie poster image for content moderation only.
Return ONLY valid JSON (no markdown), matching this exact shape:
{
  "blood_gore": 0.0,
  "violence": 0.0,
  "sexual_content": 0.0,
  "sensitive": [],
  "moderation_notes": "one short sentence on sensitive content, or empty"
}
blood_gore/violence/sexual_content: likelihood 0..1 that the poster artwork
ITSELF genuinely depicts that content in a realistic, graphic way -- not
merely whether it references or stylizes it.

Score LOW (below 0.3): illustrated/painted blood splatter, cartoonish or
silhouetted violence, stylized horror-genre artwork, designed poster art
that reads as art rather than a real depiction. This is normal, expected
content for a horror movie poster -- do not flag it just for being
present.

Score HIGH (0.5 or above) only when the depiction looks photorealistic or
graphically explicit -- like it could be a real photograph of real gore,
violence, or nudity, not clearly stylized/illustrated artwork.

"sensitive": a list of zero or more tags from this exact set that apply --
violence, gore, nudity, sexual, occult, self-harm -- and [] if none apply.
Never include the word "none" as a tag; use an empty list instead.
"""

OUT_FIELDS = [
    "id", "title", "poster_path",
    "nova_blood_gore", "nova_violence", "nova_sexual_content", "nova_sensitive", "nova_moderation_notes",
    "rek_gore", "rek_violence", "rek_mod_weapons", "rek_nudity", "rek_suggestive", "rek_mod_raw",
    "flagged", "flag_reasons", "error",
]

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


def _score(val, default: float = 0.0) -> float:
    """Ported as-is from the real nova_poster_enrich.py's _score()."""
    try:
        x = float(val)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, x)), 4)


def score_nova(bedrock, img_bytes: bytes, model_id: str) -> dict:
    body = {
        "messages": [{
            "role": "user",
            "content": [
                {"image": {"format": "jpeg", "source": {"bytes": img_bytes}}},
                {"text": MODERATION_PROMPT},
            ],
        }],
        "inferenceConfig": {"maxTokens": 300, "temperature": 0},
    }
    result = bedrock.converse(modelId=model_id, messages=body["messages"], inferenceConfig=body["inferenceConfig"])
    text = result["output"]["message"]["content"][0]["text"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(text)
    return {
        "nova_blood_gore": _score(data.get("blood_gore")),
        "nova_violence": _score(data.get("violence")),
        "nova_sexual_content": _score(data.get("sexual_content")),
        "nova_sensitive": "|".join(str(t) for t in (data.get("sensitive") or [])),
        "nova_moderation_notes": str(data.get("moderation_notes") or "").replace("\n", " ").strip()[:300],
    }


def _mod_score(mods: list[tuple[str, float]], *names: str) -> float:
    """Ported as-is from the real rekognition_enrich.py's _mod_score()."""
    want = {n.lower() for n in names}
    best = 0.0
    for name, conf in mods:
        if name.lower() in want:
            best = max(best, conf)
    return round(best, 4)


def score_rekognition(client, img_bytes: bytes, attempts: int = 6) -> dict:
    last_exc = None
    for attempt in range(attempts):
        try:
            resp = client.detect_moderation_labels(Image={"Bytes": img_bytes}, MinConfidence=40)
            break
        except ClientError as e:
            last_exc = e
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ThrottlingException", "ProvisionedThroughputExceededException"):
                time.sleep(min(20, 1.5 * (2 ** attempt)))
                continue
            raise
    else:
        raise last_exc  # type: ignore[misc]

    mods = [(m["Name"], float(m["Confidence"]) / 100.0) for m in resp.get("ModerationLabels", [])]
    mod_raw = "|".join(f"{n}:{c:.2f}" for n, c in mods[:8])
    return {
        "rek_gore": _mod_score(mods, "Visually Disturbing", "Blood & Gore", "Gore", "Emaciated Bodies", "Corpses", "Hanging"),
        "rek_violence": _mod_score(mods, "Violence", "Graphic Violence"),
        "rek_mod_weapons": _mod_score(mods, "Weapons"),
        "rek_nudity": _mod_score(mods, "Explicit Nudity", "Nudity", "Partial Nudity", "Graphic Male Nudity", "Graphic Female Nudity"),
        "rek_suggestive": _mod_score(mods, "Suggestive", "Revealing Clothes"),
        "rek_mod_raw": mod_raw,
    }


def compute_flag(row: dict) -> tuple[bool, list[str]]:
    """Pure function: real thresholds (NOVA_THRESHOLD=0.5, REK_THRESHOLD=0.4)
    applied across both engines' fields. A row can be flagged by either
    engine independently -- doesn't require both to agree, since either
    one crossing its own threshold is real project-cited evidence of
    concerning content, not something that needs a second confirmation to
    act on (unlike gates 5/8-9's title-match logic, where disagreement
    between engines is itself the interesting finding)."""
    reasons = []
    for field, thr in [
        ("nova_blood_gore", NOVA_THRESHOLD), ("nova_violence", NOVA_THRESHOLD), ("nova_sexual_content", NOVA_THRESHOLD),
        ("rek_gore", REK_THRESHOLD), ("rek_violence", REK_THRESHOLD),
        ("rek_nudity", REK_THRESHOLD), ("rek_suggestive", REK_THRESHOLD),
    ]:
        val = row.get(field)
        if val is not None and val != "" and float(val) >= thr:
            reasons.append(f"{field}>={thr}")
    return bool(reasons), reasons


def process_one(row: dict, bedrock, rekognition, session: requests.Session, model_id: str) -> dict:
    pid, title, poster_path = row["id"], row.get("title", ""), row.get("poster_path", "")
    out = {"id": pid, "title": title, "poster_path": poster_path, "error": ""}
    try:
        resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=15)
        resp.raise_for_status()
        img_bytes = resize_jpeg(resp.content)

        out.update(score_nova(bedrock, img_bytes, model_id))
        out.update(score_rekognition(rekognition, img_bytes))
    except Exception as e:
        out["error"] = str(e)[:300]
        for f in OUT_FIELDS:
            out.setdefault(f, "")

    flagged, reasons = compute_flag(out)
    out["flagged"] = int(flagged)
    out["flag_reasons"] = "|".join(reasons)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", default="data/sample_output/content_moderation.csv")
    ap.add_argument("--model", default=DEFAULT_MODEL_ID)
    ap.add_argument("--region", default=None, help="overrides AWS_DEFAULT_REGION/utils.constants.AWS_REGION")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    bedrock = get_client("bedrock-runtime", args.region) if args.region else get_client("bedrock-runtime")
    rekognition = get_client("rekognition", args.region) if args.region else get_client("rekognition")

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_path = Path(args.out)
    done = load_done_ids(out_path)
    todo = [r for r in rows if r["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo)} remaining")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = out_path.exists() and out_path.stat().st_size > 0
    out_f = out_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_f, fieldnames=OUT_FIELDS)
    if not file_exists:
        writer.writeheader()
        out_f.flush()

    n_flagged = 0
    n_done = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process_one, row, bedrock, rekognition, requests.Session(), args.model): row
                for row in todo
            }
            for fut in as_completed(futures):
                result = fut.result()
                with _write_lock:
                    writer.writerow(result)
                    out_f.flush()
                n_done += 1
                if result.get("flagged") == 1:
                    n_flagged += 1
                if n_done % 25 == 0 or n_done == len(todo):
                    log.info(f"{n_done}/{len(todo)} (flagged so far: {n_flagged})")
    finally:
        out_f.close()

    log.info(f"wrote {out_path} ({n_done} this run, {n_flagged} flagged)")


if __name__ == "__main__":
    main()
