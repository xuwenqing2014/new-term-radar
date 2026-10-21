"""Failed requests do not retry, pause later requests, or stop the radar."""
import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from radar import goanyapi

# conftest disables paid calls; retain the real implementation for mocked HTTP tests.
_call = goanyapi.call


@pytest.fixture(autouse=True)
def credit_state(monkeypatch):
    monkeypatch.setenv("GOANYAPI_API_KEY", "test-key")


def response(body):
    return io.StringIO(json.dumps(body))


@pytest.mark.parametrize("status,body", [
    (402, "Payment Required"),
    (403, '{"code":"INSUFFICIENT_CREDITS"}'),
    (429, '{"message":"Not enough credits"}'),
    (503, '{"message":"余额不足"}'),
    (200, {"code": "insufficient_credits", "message": "Top up"}),
    (200, {"code": "error", "message": "Credit balance exhausted"}),
])
def test_credit_errors_do_not_pause_later_requests(monkeypatch, status, body):
    def fail(*args, **kwargs):
        if status == 200:
            return response(body)
        raise urllib.error.HTTPError(
            "https://example.test", status, "error", {}, io.BytesIO(body.encode()))
    http = Mock(side_effect=fail)
    monkeypatch.setattr(goanyapi.urllib.request, "urlopen", http)
    for path in ("google-trends", "dr", "chrome-web-store-reviews"):
        with pytest.raises(goanyapi.GoAnyError, match="credits exhausted"):
            _call(path)
    assert http.call_count == 3


def test_next_request_can_succeed_immediately(monkeypatch):
    http = Mock(side_effect=[
        response({"code": "insufficient_credits"}),
        response({"code": "ok", "data": {"costCredits": 6}}),
    ])
    monkeypatch.setattr(goanyapi.urllib.request, "urlopen", http)
    with pytest.raises(goanyapi.GoAnyError):
        _call("google-trends")
    assert _call("google-trends") == ({"costCredits": 6}, 6000)
    assert http.call_count == 2


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("https://example.test", 502, "error", {}, io.BytesIO(b"Bad Gateway")),
    TimeoutError("timed out"),
    ValueError("Invalid JSON"),
])
def test_failure_does_not_retry(monkeypatch, failure):
    http = Mock(side_effect=[
        failure,
        response({"code": "ok", "data": {}}),
    ])
    monkeypatch.setattr(goanyapi.urllib.request, "urlopen", http)
    with pytest.raises(goanyapi.GoAnyError):
        _call("google-trends")
    assert http.call_count == 1
    assert _call("google-trends") == ({}, 0)
    assert http.call_count == 2


def test_rate_limit_is_not_credit_exhaustion(monkeypatch):
    http = Mock(side_effect=urllib.error.HTTPError(
        "https://example.test", 429, "error", {}, io.BytesIO(b"Rate limit exceeded")))
    monkeypatch.setattr(goanyapi.urllib.request, "urlopen", http)
    with pytest.raises(goanyapi.GoAnyError, match="HTTP 429"):
        _call("google-trends")
    assert http.call_count == 1
