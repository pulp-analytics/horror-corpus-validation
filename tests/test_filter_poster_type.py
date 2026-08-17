"""Unit tests for 04_filter_poster_type.py, using botocore.stub.Stubber for
both the bedrock-runtime and rekognition clients -- no real AWS credentials
or network calls needed. Live validation numbers (91.5% accuracy / 83.9%
precision / 84.4% recall on all 2,528 scoreable rows of
data/ground_truth/poster_type_human_labels.csv) are recorded in
docs/RESULTS.md, not re-run here -- these tests only exercise the parsing/
routing logic, matching the pattern in test_bedrock_ocr.py."""
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

gate4 = importlib.import_module("04_filter_poster_type")


def converse_response(text: str) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        "metrics": {"latencyMs": 250},
    }


def make_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), color=(80, 80, 80)).save(buf, format="JPEG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content: bytes = b"", status: int = 200):
        self.content = content
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.HTTPError(f"{self._status} error fetching poster")


class FakeSession:
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


@pytest.fixture
def rekognition_stub():
    client = boto3.client("rekognition", region_name="us-east-1",
                           aws_access_key_id="test", aws_secret_access_key="test")
    stubber = Stubber(client)
    stubber.activate()
    yield client, stubber
    stubber.deactivate()


def text_detection_response(lines: list[str]) -> dict:
    return {"TextDetections": [
        {"DetectedText": t, "Type": "LINE", "Confidence": 99.0, "Id": i,
         "Geometry": {"BoundingBox": {"Width": 0.5, "Height": 0.1, "Left": 0.1, "Top": 0.1},
                      "Polygon": []}}
        for i, t in enumerate(lines)
    ]}


# ---------------------------------------------------------------------------
# has_real_text
# ---------------------------------------------------------------------------

def test_has_real_text_true_when_lines_detected(rekognition_stub):
    client, stubber = rekognition_stub
    stubber.add_response("detect_text", text_detection_response(["THE RING"]))
    assert gate4.has_real_text(client, make_jpeg_bytes()) is True


def test_has_real_text_false_when_no_detections(rekognition_stub):
    client, stubber = rekognition_stub
    stubber.add_response("detect_text", text_detection_response([]))
    assert gate4.has_real_text(client, make_jpeg_bytes()) is False


def test_has_real_text_ignores_blank_detected_text(rekognition_stub):
    client, stubber = rekognition_stub
    stubber.add_response("detect_text", text_detection_response(["   "]))
    assert gate4.has_real_text(client, make_jpeg_bytes()) is False


# ---------------------------------------------------------------------------
# classify_poster_type -- Nova response parsing
# ---------------------------------------------------------------------------

def test_classify_poster_type_parses_clean_json(bedrock_stub):
    client, stubber = bedrock_stub
    stubber.add_response("converse", converse_response('{"is_movie_poster": false, "confidence": "high"}'))
    result = gate4.classify_poster_type(client, make_jpeg_bytes(), "us.amazon.nova-pro-v1:0")
    assert result == {"is_movie_poster": False, "confidence": "high"}


def test_classify_poster_type_strips_markdown_fence(bedrock_stub):
    client, stubber = bedrock_stub
    stubber.add_response("converse", converse_response(
        '```json\n{"is_movie_poster": true, "confidence": "medium"}\n```'))
    result = gate4.classify_poster_type(client, make_jpeg_bytes(), "us.amazon.nova-pro-v1:0")
    assert result == {"is_movie_poster": True, "confidence": "medium"}


# ---------------------------------------------------------------------------
# process_one -- the deterministic pre-filter must skip Nova entirely when
# real text is found (only one stubbed rekognition response queued; if the
# code called bedrock anyway there'd be no stubbed response for it to use)
# ---------------------------------------------------------------------------

def test_process_one_skips_nova_when_text_present(bedrock_stub, rekognition_stub):
    bedrock_client, bedrock_stubber = bedrock_stub
    rek_client, rek_stubber = rekognition_stub
    rek_stubber.add_response("detect_text", text_detection_response(["SOME TITLE"]))

    row = {"id": "1", "title": "Some Movie", "poster_path": "/poster.jpg"}
    out = gate4.process_one(row, bedrock_client, rek_client,
                             FakeSession(FakeResponse(make_jpeg_bytes())), "us.amazon.nova-pro-v1:0")

    assert out["method"] == "ocr_text_present"
    assert out["is_movie_poster"] is True
    assert out["error"] == ""
    bedrock_stubber.assert_no_pending_responses()  # never called


def test_process_one_calls_nova_when_no_text(bedrock_stub, rekognition_stub):
    bedrock_client, bedrock_stubber = bedrock_stub
    rek_client, rek_stubber = rekognition_stub
    rek_stubber.add_response("detect_text", text_detection_response([]))
    bedrock_stubber.add_response("converse", converse_response('{"is_movie_poster": false, "confidence": "high"}'))

    row = {"id": "2", "title": "Some Movie", "poster_path": "/poster.jpg"}
    out = gate4.process_one(row, bedrock_client, rek_client,
                             FakeSession(FakeResponse(make_jpeg_bytes())), "us.amazon.nova-pro-v1:0")

    assert out["method"] == "nova_zero_ocr"
    assert out["is_movie_poster"] is False
    assert out["nova_confidence"] == "high"


def test_process_one_missing_poster_path_errors_without_any_call(bedrock_stub, rekognition_stub):
    bedrock_client, bedrock_stubber = bedrock_stub
    rek_client, rek_stubber = rekognition_stub

    row = {"id": "3", "title": "Some Movie", "poster_path": ""}
    out = gate4.process_one(row, bedrock_client, rek_client,
                             FakeSession(FakeResponse(b"", status=404)), "us.amazon.nova-pro-v1:0")

    assert out["error"] == "no poster_path"
    bedrock_stubber.assert_no_pending_responses()
    rek_stubber.assert_no_pending_responses()


# ---------------------------------------------------------------------------
# compute_validate_metrics -- pure function
# ---------------------------------------------------------------------------

def test_compute_validate_metrics_perfect_agreement():
    results = [
        {"human_verdict": "es_poster", "is_movie_poster": "True", "error": ""},
        {"human_verdict": "no_es_poster", "is_movie_poster": "False", "error": ""},
    ]
    m = gate4.compute_validate_metrics(results)
    assert m["n_scored"] == 2
    assert m["accuracy"] == 1.0
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0


def test_compute_validate_metrics_excludes_errors_and_unjudged():
    results = [
        {"human_verdict": "es_poster", "is_movie_poster": "True", "error": ""},
        {"human_verdict": "es_poster", "is_movie_poster": "", "error": "download failed"},
        {"human_verdict": "no_seguro", "is_movie_poster": "True", "error": ""},
    ]
    m = gate4.compute_validate_metrics(results)
    assert m["n_scored"] == 1
    assert m["n_errored"] == 2


def test_compute_validate_metrics_none_when_nothing_flagged_true():
    """No TP/FP means precision is undefined (0/0) -- must be None, not a
    ZeroDivisionError, and print_validate_report must handle it (real bug
    caught live 2026-08-17: the first version crashed here)."""
    results = [{"human_verdict": "no_es_poster", "is_movie_poster": "False", "error": ""}]
    m = gate4.compute_validate_metrics(results)
    assert m["precision"] is None
    gate4.print_validate_report(results)  # must not raise
