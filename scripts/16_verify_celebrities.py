#!/usr/bin/env python3
"""Celebrity-recognition gate: flags posters where Amazon Rekognition's
RecognizeCelebrities finds a real, identifiable person who has nothing to
do with the film -- a strong signal the poster art was recycled from an
unrelated photo, not a false-positive worth ignoring.

Ported from the real project's own three-step process
(recognize_celebrities.py -> verify_celebrities_vs_cast.py ->
verify_celebrities_claude.py), folded into one resumable per-poster gate
here rather than three separate scripts, since all three steps operate on
the same row and this repo's other multi-signal gates (15) already
combine independent checks in one pass.

Step 1 -- detect (Rekognition RecognizeCelebrities): who does Rekognition
think is in this poster?

Step 2 -- deterministic cross-check: is that name in this film's real
TMDB cast/crew (`/movie/{id}/credits`)? Fuzzy name_match() ported as-is
from the real script (exact / substring / >0.85 SequenceMatcher ratio) --
handles "Robert De Niro" vs "Robert DeNiro" formatting differences without
also accepting two genuinely different people.

Step 3 -- LLM plausibility (Nova), for names that fail step 2 only: a
plain string mismatch isn't enough to flag a poster as recycled art --
the real project's own live run found 20,181 posters with a celebrity
match, of which only 13,479 failed the cast cross-check, and of THOSE,
a follow-up Nova pass found real, meaningful spread: some were obvious
Rekognition errors (identifying a historical figure who died decades
before the film, or before photography existed at all), a lot were
plausible (real industry people -- extras, uncredited actors -- who just
don't appear in TMDB's cast list), and a real remainder was genuinely
ambiguous. See docs/RESULTS.md once this gate has a live run recorded.

Only `clearly_wrong` is flagged for exclusion -- `plausible`/`uncertain`
verdicts are recorded but NOT flagged, since treating every cast mismatch
as recycled art would flag real actors just because TMDB's own credits
are incomplete, the opposite of what this gate is for. This mirrors gate
15's real-thresholds discipline: don't invent a cutoff, use what the
follow-up plausibility check actually distinguishes.

NOT YET LIVE-VERIFIED as a public port -- this repo's own norm is every
threshold gets checked against a real, blind human review before
docs/RESULTS.md cites an accuracy number (see "Validation methodology" in
the README). This gate's Nova plausibility prompt and clearly_wrong
threshold are ported from the real project's own already-run numbers
above, not invented, but haven't yet had their own live run + blind
human-review pass on this repo's corpus the way gates 4/6/15 have.
scripts/qa/build_celebrity_review_page.py is ready for that pass once
credentials are available.

Usage:
  TMDB_API_KEY=... AWS_PROFILE=... python3 16_verify_celebrities.py \\
      --in data/sample_output/validated_corpus.csv --workers 8

Resumable: --out is the checkpoint, one row per id, same pattern as gate
15 (open_for_append + skip ids already present).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

import requests
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent))
from utils.aws_config import get_client, get_tmdb_key
from utils.logging_setup import get_logger
from utils.resumable import load_done_ids, open_for_append, shard_rows
from utils.tmdb_client import IMAGE_BASE_URL, tmdb_get

log = get_logger("verify_celebrities")

DEFAULT_MODEL_ID = "us.amazon.nova-pro-v1:0"
TMDB_IMG = f"{IMAGE_BASE_URL}w780"

PLAUSIBILITY_PROMPT = """You are a data-verification assistant for a film-poster research project.
A facial-recognition system (AWS Rekognition RecognizeCelebrities) found a
person on a movie poster who does NOT appear in that film's real TMDB
cast/crew list. For each name below, judge whether:

- "clearly_wrong": Rekognition is clearly mistaken -- the person died
  before the photograph could have been taken or before film existed, is
  a historical/political/scientific figure with no plausible connection
  to a film of this genre/era, or the name makes no sense for this
  film's year/genre.
- "plausible": a real actor, extra, stunt double, or industry person who
  genuinely could appear on this poster (uncredited, alias, or simply not
  in TMDB's cast list), even though TMDB doesn't list them.
- "uncertain": not enough information to decide confidently.

Film: "{title}" ({year})
Names to judge: {names}

Respond with ONLY valid JSON, no markdown, matching this exact shape:
{{"results": [{{"name": "<exact name>", "verdict": "clearly_wrong"|"plausible"|"uncertain", "reason": "<one short sentence>"}}, ...]}}
Include exactly one entry per name listed above.
"""

OUT_FIELDS = [
    "id", "title", "poster_path",
    "n_celebs", "celeb_names", "cast_match", "unmatched_celebs",
    "verdicts", "flagged", "flag_reasons", "error",
]

_write_lock = threading.Lock()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def name_match(a: str, b: str) -> bool:
    """Ported as-is from the real verify_celebrities_vs_cast.py's name_match()."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() > 0.85


def cross_check_cast(session: requests.Session, api_key: str, pid: str, names: list[str]) -> tuple[list[str], list[str]]:
    """Returns (matched, unmatched) against the film's real TMDB credits."""
    resp = tmdb_get(session, api_key, f"movie/{pid}/credits")
    data = resp.json() if resp.ok else {}
    cast_names = [c.get("name", "") for c in (data.get("cast") or [])]
    crew_names = [c.get("name", "") for c in (data.get("crew") or [])]
    all_names = cast_names + crew_names

    matched, unmatched = [], []
    for n in names:
        (matched if any(name_match(n, c) for c in all_names) else unmatched).append(n)
    return matched, unmatched


def judge_plausibility(bedrock, title: str, year, names: list[str], model_id: str) -> list[dict]:
    prompt = PLAUSIBILITY_PROMPT.format(
        title=title or "?", year=int(year) if year not in (None, "", "nan") else "?",
        names=", ".join(f'"{n}"' for n in names),
    )
    result = bedrock.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"temperature": 0, "maxTokens": 1000},
    )
    text = result["output"]["message"]["content"][0]["text"].strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(text)
    results = data.get("results") or []
    # guard against the model dropping a name from its own response --
    # every input name gets a verdict, "uncertain" if the model didn't return one
    by_name = {r.get("name", ""): r for r in results}
    return [
        {"name": n, "verdict": by_name.get(n, {}).get("verdict", "uncertain"),
         "reason": by_name.get(n, {}).get("reason", "")}
        for n in names
    ]


def process_one(row: dict, rekognition, bedrock, session: requests.Session, api_key: str, model_id: str) -> dict:
    pid, title, poster_path = row["id"], row.get("title", ""), row.get("poster_path", "")
    year = row.get("year", "")
    out = {
        "id": pid, "title": title, "poster_path": poster_path,
        "n_celebs": 0, "celeb_names": "[]", "cast_match": "[]", "unmatched_celebs": "[]",
        "verdicts": "[]", "flagged": 0, "flag_reasons": "", "error": "",
    }
    try:
        img_resp = session.get(f"{TMDB_IMG}{poster_path}", timeout=15)
        img_resp.raise_for_status()
        rek_resp = rekognition.recognize_celebrities(Image={"Bytes": img_resp.content})
        celebs = rek_resp.get("CelebrityFaces", []) or []
        names = [c.get("Name", "") for c in celebs]
        out["n_celebs"] = len(names)
        out["celeb_names"] = json.dumps(names, ensure_ascii=False)

        if not names:
            return out

        matched, unmatched = cross_check_cast(session, api_key, pid, names)
        out["cast_match"] = json.dumps(matched, ensure_ascii=False)
        out["unmatched_celebs"] = json.dumps(unmatched, ensure_ascii=False)

        if not unmatched:
            return out

        verdicts = judge_plausibility(bedrock, title, year, unmatched, model_id)
        out["verdicts"] = json.dumps(verdicts, ensure_ascii=False)

        reasons = [f"celebrity_not_in_cast:{v['name']}" for v in verdicts if v["verdict"] == "clearly_wrong"]
        out["flagged"] = int(bool(reasons))
        out["flag_reasons"] = "|".join(reasons)
    except ClientError as e:
        out["error"] = str(e)[:300]
    except Exception as e:
        out["error"] = str(e)[:300]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", default="data/sample_output/celebrity_verification.csv")
    ap.add_argument("--model", default=DEFAULT_MODEL_ID)
    ap.add_argument("--region", default=None, help="overrides AWS_DEFAULT_REGION/utils.constants.AWS_REGION")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1, help="split --in across N parallel shards (default 1: no sharding)")
    args = ap.parse_args()

    api_key = get_tmdb_key()
    rekognition = get_client("rekognition", args.region) if args.region else get_client("rekognition")
    bedrock = get_client("bedrock-runtime", args.region) if args.region else get_client("bedrock-runtime")

    import csv
    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = shard_rows(rows, args.shard_index, args.shard_count)

    out_path = Path(args.out)
    done = load_done_ids(out_path)
    todo = [r for r in rows if r["id"] not in done]
    if done:
        log.info(f"resuming: {len(done)} already done, {len(todo)} remaining")

    out_f, writer = open_for_append(out_path, OUT_FIELDS)

    n_done = 0
    n_flagged = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process_one, row, rekognition, bedrock, requests.Session(), api_key, args.model): row
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
