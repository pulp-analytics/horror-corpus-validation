"""AWS client setup shared by the Comprehend/Translate/Bedrock/Rekognition scripts."""
from __future__ import annotations

import os

import boto3
from botocore.config import Config

from .constants import AWS_REGION


def get_client(service: str, region: str = AWS_REGION):
    """boto3 client with sane retry/timeout defaults for batch API calls."""
    config = Config(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=10, read_timeout=30)
    return boto3.client(service, region_name=region, config=config)


def get_tmdb_key() -> str:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        raise SystemExit("TMDB_API_KEY not set — copy .env.example to .env and fill it in")
    return key
