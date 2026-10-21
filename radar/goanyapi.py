"""Optional second data source: GoAnyAPI (goanyapi.com), for what AIsa's tools do not give.

Used for two things: Google Trends' *related queries* for a name, the terms real people
searched together with it, and a 30-day daily Trends series for one country, which
arrives settled up to yesterday (DataForSEO's worldwide series does not). Key: GOANYAPI_API_KEY. Without it every caller skips
this source and the radar runs as before.

One credit is $0.001; a related-queries lookup costs 6 (October 2026). Calls take 20 to
55 seconds and about one in ten fails with a timeout or a 502. Each call is attempted
once; a failure is reported, never fatal, and subsequent calls proceed normally. The service is young (first released August
2026): treat it as an enhancement, not a dependency.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import UA

BASE = os.environ.get("GOANYAPI_BASE_URL", "https://api.goanyapi.com") + "/api/v1/"
TIMEOUT = 90
MICROS_PER_CREDIT = 1000
CREDIT_ERROR = "GoAnyAPI credits exhausted; continuing with available sources"


class GoAnyError(RuntimeError):
    pass


def key() -> str:
    return os.environ.get("GOANYAPI_API_KEY", "").strip()


def _credits_exhausted(body: object) -> bool:
    """Recognize credit errors in both JSON error codes and plain-text messages."""
    message = re.sub(r"[_-]+", " ", str(body).lower())
    return bool(re.search(
        r"(?:insufficient|exhausted|depleted|not enough|out of|no remaining)\s+(?:\w+\s+){0,2}(?:credits?|balance|quota)"
        r"|(?:credits?|balance|quota)\s+(?:\w+\s+){0,2}(?:insufficient|exhausted|depleted|exceeded)"
        r"|积分不足|余额不足|额度不足|积分耗尽|配额耗尽", message))


def call(path: str, **params) -> tuple[dict, int]:
    """One GET, without retries or pauses. Returns (data, cost in micro-USD)."""
    req = urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(params), headers={
        "authorization": f"Bearer {key()}", "accept": "application/json", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            body = json.load(res)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(8192).decode("utf-8", errors="replace")
        except OSError:
            detail = ""
        if exc.code == 402 or _credits_exhausted(detail):
            raise GoAnyError(CREDIT_ERROR) from None
        raise GoAnyError(f"HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise GoAnyError(f"{type(exc).__name__}: {exc}"[:160]) from None
    if not isinstance(body, dict):
        raise GoAnyError("Invalid GoAnyAPI response")
    if body.get("code") != "ok":
        if _credits_exhausted(body):
            raise GoAnyError(CREDIT_ERROR)
        raise GoAnyError(str(body.get("message") or body)[:160])
    data = body.get("data") or {}
    return data, int(data.get("costCredits") or data.get("creditsUsed") or 0) * MICROS_PER_CREDIT


def related_queries(term: str, geo: str = "US", window: str = "today 1-m") -> tuple[dict[str, list[str]], int]:
    """{"rising": [...], "top": [...]}: what people searched on Google together with `term`."""
    data, cost = call("google-trends", q=term, date=window, geo=geo, data_type="RELATED_QUERIES")
    rq = data.get("relatedQueries") or {}
    return {k: [" ".join(str(x.get("query", "")).lower().split()) for x in rq.get(k) or []] for k in ("rising", "top")}, cost


def daily_series(term: str, geo: str = "US", window: str = "today 1-m") -> tuple[list[tuple[date, int]], int]:
    """[(day, value)] for `term` in one country, the last month by day."""
    data, cost = call("google-trends", q=term, date=window, geo=geo, data_type="TIMESERIES")
    out = []
    for p in (data.get("interestOverTime") or {}).get("timeline_data") or []:
        try:
            day = datetime.fromtimestamp(int(p["timestamp"]), timezone.utc).date()
            out.append((day, int(p["values"][0]["extracted_value"])))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out, cost


def serp(q: str, gl: str = "US") -> tuple[list[dict], int]:
    """Google's organic results for a query (2 credits): [{"url", "title", "rank"}]."""
    data, cost = call("serp", q=q, gl=gl, hl="en")
    return [{"url": o.get("link") or "", "title": o.get("title") or "", "rank": o.get("rank")}
            for o in (data.get("result") or {}).get("organic") or []], cost


def intitle(term: str, window: str = "m") -> tuple[int | None, int]:
    """How many pages Google holds with `term` in the title, from the last week or month (4 credits)."""
    data, cost = call("google-intitle", intitle=term, gl="us", qdr=window)
    count = data.get("intitleCount")
    return (int(count) if isinstance(count, (int, float)) or str(count).isdigit() else None), cost


def advertisers(keyword: str) -> tuple[list[str], int]:
    """Domains and advertisers that run Google ads for a keyword (Ads transparency, 4 credits)."""
    data, cost = call("transparency", keyword=keyword)
    names = [str(a.get("name") or a) if isinstance(a, dict) else str(a) for a in data.get("advertisers") or []]
    return list(dict.fromkeys([*(data.get("domains") or []), *names])), cost


def chrome_reviews(extension_id: str, cursor: str = "") -> tuple[list[dict], str, int]:
    """One page of a Chrome Web Store extension's reviews (3 credits): ([{"rating", "text", "date"}], next cursor, cost)."""
    params = {"extensionId": extension_id, **({"cursor": cursor} if cursor else {})}
    data, cost = call("chrome-web-store-reviews", **params)
    rows = [{"rating": c.get("rating"), "text": " ".join(str(c.get("content") or "").split()), "date": str(c.get("createdAt") or "")[:10]}
            for c in data.get("comments") or []]
    return rows, (data.get("nextCursor") or "") if data.get("hasMore") else "", cost
