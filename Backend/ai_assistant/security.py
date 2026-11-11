from __future__ import annotations

import re
import time
from functools import wraps
from typing import Any

from flask import jsonify, request

from login import get_current_identity

ALLOWED_AI_ROLES = {"executive", "admin"}
MAX_MESSAGE_LENGTH = 1000
MAX_CONVERSATION_MESSAGES = 12
MAX_CONVERSATION_MESSAGE_LENGTH = 700
RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMIT_MAX_REQUESTS = 20

_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
_WHITESPACE_RE = re.compile(r"\s+")
_SENSITIVE_OUTPUT_PATTERNS = [
    re.compile(r"mongodb\+srv://[^\s]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(?:api[_ -]?key|secret|password|token)\s*[:=]\s*\S+", re.IGNORECASE),
]


def _normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = _CONTROL_CHARS_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def sanitize_user_message(value: Any, *, max_length: int = MAX_MESSAGE_LENGTH) -> str:
    text = _normalize_text(value)
    if len(text) > max_length:
        text = text[:max_length].rstrip()
    return text


def sanitize_conversation(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []

    cleaned: list[dict[str, str]] = []
    for item in messages[:MAX_CONVERSATION_MESSAGES]:
        if not isinstance(item, dict):
            continue
        role = _normalize_text(item.get("role")).lower()
        if role not in {"user", "assistant"}:
            continue
        content = sanitize_user_message(
            item.get("content"),
            max_length=MAX_CONVERSATION_MESSAGE_LENGTH,
        )
        if not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned


def redact_sensitive_output(text: str) -> str:
    cleaned = text or ""
    for pattern in _SENSITIVE_OUTPUT_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned.strip()


def _get_rate_limit_key(identity: dict[str, Any]) -> str:
    user_id = str(identity.get("user_id") or "").strip()
    role = str(identity.get("role") or "").strip().lower()
    if user_id:
        return f"user:{role}:{user_id}"
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "unknown")
    return f"ip:{ip}"


def _check_rate_limit(identity: dict[str, Any]) -> bool:
    key = _get_rate_limit_key(identity)
    now = time.time()
    bucket = _RATE_LIMIT_BUCKETS.get(key, [])
    bucket = [timestamp for timestamp in bucket if now - timestamp < RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        _RATE_LIMIT_BUCKETS[key] = bucket
        return False
    bucket.append(now)
    _RATE_LIMIT_BUCKETS[key] = bucket
    return True


def executive_ai_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        identity = get_current_identity()
        role = str(identity.get("role") or "").strip().lower()
        if role not in ALLOWED_AI_ROLES:
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        if not _check_rate_limit(identity):
            return jsonify({"ok": False, "error": "Rate limit exceeded. Please try again shortly."}), 429
        return view_func(*args, **kwargs)

    return wrapped
