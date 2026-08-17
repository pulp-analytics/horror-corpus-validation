"""Shared "how complete/curated is this TMDB entry" signal.

Used by both 08_dedupe_tmdb_metadata.py (same film under two ids) and
09_dedupe_poster_md5.py (same poster image under two ids) to decide which
id to keep once a duplicate group is confirmed real. One canonical
definition instead of each gate inventing its own proxy: a 4-signal
cascade built entirely from what TMDB itself exposes, each signal only
breaking a tie left by the one before it.

  1. `imdb_id` present -- cross-referenced to IMDb, a real curation signal.
  2. cast+crew count (`/credits`) -- richer credit data.
  3. official trailer present (`/videos`, any `type == "Trailer"`).
  4. TMDB's own `popularity` score, as a last resort.
"""
from __future__ import annotations

import requests

from utils.tmdb_client import tmdb_get


def get_completeness_signals(session: requests.Session, api_key: str, movie_id: str) -> dict:
    """One id -> {alive, has_imdb_id, credits, has_trailer, popularity}.
    Three TMDB calls (details, credits, videos); details alone already
    carries alive/imdb_id/popularity, so this is the minimum needed for
    the full cascade."""
    resp = tmdb_get(session, api_key, f"movie/{movie_id}")
    if resp.status_code != 200:
        return {"alive": 0, "has_imdb_id": 0, "credits": 0, "has_trailer": 0, "popularity": 0.0}
    details = resp.json()

    credits_resp = tmdb_get(session, api_key, f"movie/{movie_id}/credits")
    credits_count = 0
    if credits_resp.status_code == 200:
        d = credits_resp.json()
        credits_count = len(d.get("cast", [])) + len(d.get("crew", []))

    videos_resp = tmdb_get(session, api_key, f"movie/{movie_id}/videos")
    has_trailer = False
    if videos_resp.status_code == 200:
        has_trailer = any(v.get("type") == "Trailer" for v in videos_resp.json().get("results", []))

    return {
        "alive": 1,
        "has_imdb_id": int(bool(details.get("imdb_id"))),
        "credits": credits_count,
        "has_trailer": int(has_trailer),
        "popularity": details.get("popularity", 0.0) or 0.0,
    }


def completeness_key(signals: dict) -> tuple:
    """Cascade as a sort key: tuple comparison already implements "only
    fall through to the next signal if the earlier ones tie."""
    return (
        int(signals.get("has_imdb_id") or 0),
        int(signals.get("credits") or 0),
        int(signals.get("has_trailer") or 0),
        float(signals.get("popularity") or 0.0),
    )
