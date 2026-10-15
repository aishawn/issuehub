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


def _is_valid_base_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(hostname)
        and port != 0
        and "@" not in parsed.netloc
        and not parsed.query
        and not parsed.fragment
    )


def validate_api_url() -> None:
    if not _is_valid_base_url(API):
        raise TeamAIRequestError("Invalid TEAMAI_API: use an http(s) base URL without credentials, query, or fragment")
    if any(ord(char) < 33 or ord(char) > 126 for char in API_KEY):
        raise TeamAIRequestError("Invalid TEAMAI_API_KEY: use visible ASCII characters without spaces")


def hub_base_url() -> str | None:
    """Return the Issue Hub Web base URL for human-facing view links.

    Prefer TEAMAI_WEB_URL when set; otherwise reuse TEAMAI_API. Returns None if
    the chosen value is not a valid base URL so callers can skip hints safely.
    """
    web = os.getenv("TEAMAI_WEB_URL", "").strip().rstrip("/")
    candidate = web or API
    if not _is_valid_base_url(candidate):
        return None
    return candidate


def record_view_url(record_id: int | str) -> str | None:
    """Deep link to open a record in the Issue Hub Web UI (#record=<id>)."""
    base = hub_base_url()
    if base is None:
        return None
    try:
        rid = int(record_id)
    except (TypeError, ValueError):
        return None
    if rid < 1:
        return None
    return f"{base}/#record={rid}"


def request(method: str, path: str, payload: dict | None = None, username: str | None = None):
    validate_api_url()
    if not path.startswith("/") or path.startswith("//"):
        raise TeamAIRequestError("Invalid TeamAI API path")
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-TeamAI-Key"] = API_KEY
    active_user = username if username is not None else os.getenv("TEAMAI_USER", "")
    if active_user:
        headers["X-TeamAI-User"] = urllib.parse.quote(active_user, safe="")
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
