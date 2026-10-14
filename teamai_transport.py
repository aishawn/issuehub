"""Shared, credential-safe HTTP transport for TeamAI command-line clients."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class TeamAIRequestError(RuntimeError):
    """A safe-to-display error from the TeamAI API client."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


API = os.getenv("TEAMAI_API", "http://127.0.0.1:8787").strip().rstrip("/")
API_KEY = os.getenv("TEAMAI_API_KEY", "").strip()


def validate_api_url() -> None:
    try:
        parsed = urllib.parse.urlsplit(API)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise TeamAIRequestError("Invalid TEAMAI_API: use a valid http(s) base URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or port == 0
        or "@" in parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise TeamAIRequestError("Invalid TEAMAI_API: use an http(s) base URL without credentials, query, or fragment")
    if any(ord(char) < 33 or ord(char) > 126 for char in API_KEY):
        raise TeamAIRequestError("Invalid TEAMAI_API_KEY: use visible ASCII characters without spaces")


def request(method: str, path: str, payload: dict | None = None):
    validate_api_url()
    if not path.startswith("/") or path.startswith("//"):
        raise TeamAIRequestError("Invalid TeamAI API path")
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-TeamAI-Key"] = API_KEY
    req = urllib.request.Request(f"{API.rstrip('/')}{path}", method=method, data=body, headers=headers)
    try:
        opener = urllib.request.build_opener(_RejectRedirects())
        with opener.open(req, timeout=15) as response:
            try:
                return json.loads(response.read().decode())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TeamAIRequestError("TeamAI API returned invalid JSON") from exc
    except urllib.error.HTTPError as exc:
        raise TeamAIRequestError(f"TeamAI API returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TeamAIRequestError("TeamAI API unavailable; check TEAMAI_API and network") from None
    except ValueError:
        raise TeamAIRequestError("Invalid TEAMAI_API request URL") from None
