"""Unit tests for 04_bedrock_ocr.py's Converse response parsing, using
botocore.stub.Stubber so no real AWS credentials or network calls are
needed -- Stubber intercepts the boto3 client before any HTTP request is
made and hands back a response we control. The image download itself
(requests.Session.get) isn't a boto3 call, so it's faked separately with a
minimal in-memory JPEG.

Only exercises check_poster()'s parsing, since that's the fragile part:
main()'s CSV/resume plumbing is exercised end to end by the sample data
already checked into data/sample_output/.
"""
import importlib
import io
import sys
from pathlib import Path

import boto3
import pytest
import requests
from botocore.stub import Stubber
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

bedrock_ocr = importlib.import_module("04_bedrock_ocr")


def converse_response(text: str) -> dict:
    """Minimal well-formed Converse response wrapping `text` as the model's
    reply, matching every field the bedrock-runtime service model requires."""
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        "metrics": {"latencyMs": 250},
    }


def make_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), color=(200, 50, 50)).save(buf, format="JPEG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content: bytes = b"", status: int = 200):
        self.content = content
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.HTTPError(f"{self._status} error fetching poster")


class FakeSession:
    """Stands in for requests.Session -- .get() always returns `response`."""

    def __init__(self, response: FakeResponse):
        self._response = response

    def get(self, url, timeout=None):
        return self._response


@pytest.fixture
def bedrock_stub():
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                           aws_access_key_id="test", aws_secret_access_key="test")
    stubber = Stubber(client)
    stubber.activate()
    yield client, stubber
    stubber.deactivate()


def test_check_poster_parses_clean_json(bedrock_stub):
    client, stubber = bedrock_stub
    stubber.add_response("converse", converse_response(
        '{"text_you_read": "The Ring", "verdict": "match", "reason": "matches catalog title"}'))

    result = bedrock_ocr.check_poster(client, FakeSession(FakeResponse(make_jpeg_bytes())),
                                       "/poster.jpg", "The Ring", "us.amazon.nova-pro-v1:0")

    assert result == {"text_you_read": "The Ring", "verdict": "match", "reason": "matches catalog title"}
    stubber.assert_no_pending_responses()


def test_check_poster_strips_markdown_json_fence(bedrock_stub):
    # real models often wrap the JSON in ```json ... ``` despite being told not to
    client, stubber = bedrock_stub
    stubber.add_response("converse", converse_response(
        '```json\n{"text_you_read": "Black Ops", "verdict": "mismatch", "reason": "different title"}\n```'))

    result = bedrock_ocr.check_poster(client, FakeSession(FakeResponse(make_jpeg_bytes())),
                                       "/poster.jpg", "BlackOps", "us.amazon.nova-pro-v1:0")

    assert result["verdict"] == "mismatch"
    assert result["text_you_read"] == "Black Ops"


def test_check_poster_raises_on_malformed_json(bedrock_stub):
    client, stubber = bedrock_stub
    stubber.add_response("converse", converse_response("sorry, I can't read this poster clearly."))

    with pytest.raises(ValueError):  # json.JSONDecodeError is a ValueError subclass
        bedrock_ocr.check_poster(client, FakeSession(FakeResponse(make_jpeg_bytes())),
                                  "/poster.jpg", "Some Movie", "us.amazon.nova-pro-v1:0")


def test_check_poster_download_failure_never_calls_bedrock(bedrock_stub):
    # a 404/unreachable poster should fail before spending a Bedrock call --
    # no stubbed response is queued, so if the code called converse anyway
    # Stubber would raise "no more responses" instead of the HTTPError below
    client, stubber = bedrock_stub

    with pytest.raises(requests.HTTPError):
        bedrock_ocr.check_poster(client, FakeSession(FakeResponse(b"", status=404)),
                                  "/missing.jpg", "Some Movie", "us.amazon.nova-pro-v1:0")

    stubber.assert_no_pending_responses()
