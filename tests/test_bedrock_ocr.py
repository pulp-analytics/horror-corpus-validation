"""Unit tests for 05_bedrock_ocr.py's Converse response parsing, using
botocore.stub.Stubber so no real AWS credentials or network calls are
needed -- Stubber intercepts the boto3 client before any HTTP request is
made and hands back a response we control. The image download itself
(requests.Session.get) isn't a boto3 call, so it's faked separately with a
minimal in-memory JPEG.

Only exercises check_poster()'s parsing, since that's the fragile part:
main()'s CSV/resume plumbing is exercised end to end by the sample data
already checked into data/sample_output/.
"""
import argparse
import csv
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

bedrock_ocr = importlib.import_module("05_bedrock_ocr")


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


# ---------------------------------------------------------------------------
# compute_validate_metrics -- pure function, no AWS/network involved at all
# ---------------------------------------------------------------------------

def test_compute_validate_metrics_perfect_agreement():
    results = [
        {"human_verdict": "match", "live_verdict": "match", "error": ""},
        {"human_verdict": "mismatch", "live_verdict": "mismatch", "error": ""},
        {"human_verdict": "no_title_on_poster", "live_verdict": "no_title_on_poster", "error": ""},
    ]
    m = bedrock_ocr.compute_validate_metrics(results)
    assert m["n_scored"] == 3
    assert m["n_errored"] == 0
    assert m["accuracy"] == 1.0
    for c in bedrock_ocr.VALIDATE_CLASSES:
        assert m["per_class"][c]["precision"] == 1.0
        assert m["per_class"][c]["recall"] == 1.0


def test_compute_validate_metrics_errors_excluded_from_every_count():
    results = [
        {"human_verdict": "match", "live_verdict": "match", "error": ""},
        {"human_verdict": "match", "live_verdict": "", "error": "boto3 timeout"},
    ]
    m = bedrock_ocr.compute_validate_metrics(results)
    assert m["n_scored"] == 1
    assert m["n_errored"] == 1
    assert m["accuracy"] == 1.0  # the errored row doesn't count as wrong


def test_compute_validate_metrics_false_reject_pattern():
    # mirrors the real finding: Nova said "mismatch" on several rows a
    # human confirmed were actually "match" -- precision for "mismatch"
    # should tank while recall for "match" tanks too, in opposite directions
    results = (
        [{"human_verdict": "match", "live_verdict": "mismatch", "error": ""}] * 4
        + [{"human_verdict": "match", "live_verdict": "match", "error": ""}]
        + [{"human_verdict": "mismatch", "live_verdict": "mismatch", "error": ""}]
    )
    m = bedrock_ocr.compute_validate_metrics(results)
    assert m["n_scored"] == 6
    # "mismatch" predicted 5 times, only 1 actually was -> low precision
    assert m["per_class"]["mismatch"]["precision"] == pytest.approx(1 / 5)
    # "mismatch" support is 1, and that 1 was caught -> perfect recall despite bad precision
    assert m["per_class"]["mismatch"]["recall"] == 1.0
    # "match" support is 5, only 1 predicted correctly -> low recall
    assert m["per_class"]["match"]["recall"] == pytest.approx(1 / 5)


def test_compute_validate_metrics_no_support_is_none_not_zero():
    # a class that never appears in human_verdict has no recall to report --
    # None (not 0.0), so callers don't mistake "never happened" for "always wrong"
    results = [{"human_verdict": "match", "live_verdict": "match", "error": ""}]
    m = bedrock_ocr.compute_validate_metrics(results)
    assert m["per_class"]["mismatch"]["support"] == 0
    assert m["per_class"]["mismatch"]["recall"] is None
    assert m["per_class"]["mismatch"]["precision"] is None  # never predicted either


def test_compute_validate_metrics_empty_results():
    m = bedrock_ocr.compute_validate_metrics([])
    assert m["n_scored"] == 0
    assert m["accuracy"] is None


# ---------------------------------------------------------------------------
# run_validate -- full flow: ground-truth CSV in, stubbed Bedrock calls,
# results CSV out. unjudgeable rows must never reach check_poster (no
# stubbed response queued for them -- Stubber fails loudly if they did).
# ---------------------------------------------------------------------------

def test_run_validate_skips_unjudgeable_and_writes_results(tmp_path, bedrock_stub):
    client, stubber = bedrock_stub
    gt_path = tmp_path / "ground_truth.csv"
    gt_path.write_text(
        "id,title,poster_path,stratum,human_verdict,human_note\n"
        '1,"Movie A",/a.jpg,accurate,match,""\n'
        '2,"Movie B",/b.jpg,inaccurate,mismatch,""\n'
        '3,"Movie C",/c.jpg,inaccurate,unjudgeable,""\n',
        encoding="utf-8",
    )
    # exactly two live calls expected -- id 3 (unjudgeable) must be skipped
    stubber.add_response("converse", converse_response(
        '{"text_you_read": "Movie A", "verdict": "match", "reason": "matches"}'))
    stubber.add_response("converse", converse_response(
        '{"text_you_read": "Movie X", "verdict": "match", "reason": "close enough"}'))

    validate_out = tmp_path / "results.csv"
    args = argparse.Namespace(ground_truth=str(gt_path), validate_out=str(validate_out),
                               model="us.amazon.nova-pro-v1:0", delay=0)
    session = FakeSession(FakeResponse(make_jpeg_bytes()))

    bedrock_ocr.run_validate(client, session, args)

    stubber.assert_no_pending_responses()  # id 3 never called converse
    with validate_out.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["id"] for r in rows] == ["1", "2"]
    assert rows[0]["human_verdict"] == "match" and rows[0]["live_verdict"] == "match"
    # id 2's ground truth is "mismatch" but the stub returns "match" -- a real disagreement
    assert rows[1]["human_verdict"] == "mismatch" and rows[1]["live_verdict"] == "match"
