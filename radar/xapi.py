"""Small client for xAPI's read-only Twitter capabilities.

The radar keeps its own post shape so the term extraction and reporting logic can
remain independent of the provider.  xAPI requires a numeric user id for timeline
calls, so the caller may cache the result of ``twitter.user_by_screen_name``.
"""
from __future__ import annotations

import json
import http.client
import os
import time
import urllib.error
import urllib.request
from typing import Any

ACTION_URL = os.environ.get("XAPI_ACTION_URL", "https://action.xapi.to/v1/actions/execute")
TIMEOUT = 60
XAPI_UA = "xapi-to/0.1"


class XapiError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(os.environ.get("XAPI_KEY", "").strip() or os.environ.get("XAPI_API_KEY", "").strip())


def key() -> str:
    value = (os.environ.get("XAPI_KEY") or os.environ.get("XAPI_API_KEY") or "").strip()
    if not value:
        raise XapiError("no xAPI key: set XAPI_KEY")
    return value


def call(action_id: str, input: dict[str, Any]) -> dict[str, Any]:
    """Execute one xAPI action and return its JSON envelope."""
    token = key()
    req = urllib.request.Request(
        ACTION_URL,
        data=json.dumps({"action_id": action_id, "input": input}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "XAPI-Key": token,
            # xAPI's edge rejects the project's descriptive UA with Cloudflare 1010.
            "User-Agent": XAPI_UA,
        },
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                payload = json.load(res)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:240]
            raise XapiError(f"HTTP {exc.code}: {' '.join(detail.split())}") from exc
        except (http.client.IncompleteRead, ConnectionResetError, TimeoutError) as exc:
            if attempt:
                raise XapiError(f"{type(exc).__name__}: {exc}") from exc
            time.sleep(0.5)
        except (OSError, ValueError) as exc:
            raise XapiError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise XapiError("unexpected xAPI response")
    if payload.get("success") is False or payload.get("error"):
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        raise XapiError(str(message or "xAPI action failed")[:240])
    return payload


def _objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def user_id(response: dict[str, Any]) -> str:
    """Get xAPI's ``rest_id`` from a user lookup response."""
    for obj in _objects(response):
        for field in ("rest_id", "user_id", "userId"):
            value = obj.get(field)
            if value:
                return str(value)
    for obj in _objects(response):
        if any(field in obj for field in ("screen_name", "screenName", "username")) and obj.get("id"):
            return str(obj["id"])
    raise XapiError("xAPI user lookup returned no user id")


def tweets(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the normalized tweet list from an xAPI response."""
    for obj in _objects(response):
        for field in ("tweets", "statuses", "posts", "results"):
            value = obj.get(field)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return value
    return []


def text(tweet: dict[str, Any]) -> str:
    return str(tweet.get("full_text") or tweet.get("text") or "")


def likes(tweet: dict[str, Any]) -> int:
    for field in ("favorite_count", "likeCount", "like_count", "likes"):
        try:
            return int(tweet.get(field) or 0)
        except (TypeError, ValueError):
            pass
    return 0


def url(tweet: dict[str, Any]) -> str:
    direct = tweet.get("url") or tweet.get("tweet_url") or tweet.get("link")
    if direct:
        return str(direct)
    author = tweet.get("author") or {}
    handle = author.get("screen_name") or author.get("userName") or author.get("username")
    tweet_id = tweet.get("id") or tweet.get("tweet_id")
    return f"https://x.com/{handle}/status/{tweet_id}" if handle and tweet_id else ""


def created_at(tweet: dict[str, Any]) -> str:
    return str(tweet.get("created_at") or tweet.get("createdAt") or "")


def cursor(response: dict[str, Any]) -> str:
    for obj in _objects(response):
        cursors = obj.get("cursors")
        if isinstance(cursors, dict) and cursors.get("bottom"):
            return str(cursors["bottom"])
        if obj.get("cursor_bottom"):
            return str(obj["cursor_bottom"])
    return ""
