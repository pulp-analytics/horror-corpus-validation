"""Shared TMDB v3 GET helper: injects api_key, applies a default timeout.
Callers still own status-code handling and response parsing -- those differ
enough per endpoint (empty list vs. empty string vs. bool) that folding them
in here would just move the branching, not remove it."""
from __future__ import annotations

import requests

BASE_URL = "https://api.themoviedb.org/3"

# TMDB's image CDN, not the API host above -- callers append a size
# ("w92", "w500", "w780", ...) and the poster_path. Was duplicated as a
# full hardcoded URL (same host, different size per script) in six
# different files; centralized here so there's one place that knows the
# CDN host.
IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"


def tmdb_get(session: requests.Session, api_key: str, path: str,
             params: dict | None = None, timeout: int = 15) -> requests.Response:
    """GET {BASE_URL}/{path} with api_key merged into params."""
    return session.get(f"{BASE_URL}/{path}", params={**(params or {}), "api_key": api_key}, timeout=timeout)
