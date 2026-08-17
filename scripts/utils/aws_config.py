"""AWS client setup shared by the Comprehend/Translate/Bedrock/Rekognition scripts.

Also the single place that loads .env -- every script needing TMDB_API_KEY
or AWS credentials imports get_tmdb_key() or get_client() from here (or
transitively via a script that does), so loading it once at import time
here covers all of them. Points at the repo root's .env explicitly (not
python-dotenv's default cwd/frame search) so it works the same whether a
script is run directly, via 12_validate_corpus.py's subprocess calls, via
pytest, or from a different working directory inside Docker.
`override=False` (python-dotenv's default) means a real environment
variable -- e.g. TMDB_API_KEY set as a GitHub Actions secret in CI --
always wins over whatever .env has, and load_dotenv() is a harmless no-op
if no .env file exists (CI doesn't have one; it sets the env var
directly)."""
from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

from .constants import AWS_REGION

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def get_client(service: str, region: str | None = None):
    """boto3 client with sane retry/timeout defaults for batch API calls.

    region, if not passed explicitly, comes from the AWS_DEFAULT_REGION
    environment variable (.env or a real env var) if set, otherwise falls
    back to AWS_REGION's hardcoded default. Resolved at call time, not at
    import/module-load time, so setting AWS_DEFAULT_REGION in .env after
    this module was first imported (e.g. in a test) still takes effect."""
    region = region or os.environ.get("AWS_DEFAULT_REGION", "").strip() or AWS_REGION
    config = Config(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=10, read_timeout=30)
    return boto3.client(service, region_name=region, config=config)


def get_tmdb_key() -> str:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        raise SystemExit("TMDB_API_KEY not set — copy .env.example to .env and fill it in")
    return key
