"""Shared TMDB v3 GET helper: injects api_key, applies a default timeout,
retries transient failures. Callers still own status-code handling and
response parsing -- those differ enough per endpoint (empty list vs. empty
string vs. bool) that folding them in here would just move the branching,
not remove it."""
from __future__ import annotations

import time

import requests

BASE_URL = "https://api.themoviedb.org/3"

# TMDB's image CDN, not the API host above -- callers append a size
# ("w92", "w500", "w780", ...) and the poster_path. Was duplicated as a
# full hardcoded URL (same host, different size per script) in six
# different files; centralized here so there's one place that knows the
# CDN host.
IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"


def tmdb_get(session: requests.Session, api_key: str, path: str,
             params: dict | None = None, timeout: int = 15, attempts: int = 5) -> requests.Response:
    """GET {BASE_URL}/{path} with api_key merged into params.

    Retries on read timeouts, connection errors, and 429/5xx -- a bare
    `session.get()` with no retry crashed a real, hours-long dedupe run on
    a single transient TMDB read timeout (see 10_dedupe_poster_md5.py's
    get_completeness_signals() call chain), losing no cached progress but
    forcing a manual restart. Same exponential-backoff shape as this repo's
    Bedrock/Rekognition retry loops (15_content_moderation.py), just for
    requests' exception types instead of botocore's."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = session.get(f"{BASE_URL}/{path}", params={**(params or {}), "api_key": api_key}, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = requests.exceptions.HTTPError(f"{resp.status_code} from TMDB")
                time.sleep(min(20, 1.5 * (2 ** attempt)))
                continue
            return resp
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
            time.sleep(min(20, 1.5 * (2 ** attempt)))
    raise last_exc  # type: ignore[misc]
