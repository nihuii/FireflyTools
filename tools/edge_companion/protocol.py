"""Validate and serialize the V1 Edge media-candidate protocol."""

from datetime import datetime, timedelta, timezone
import json
from urllib.parse import urlsplit

from tools.video_crawler.models import (
    EDGE_CAPTURE_TTL_SECONDS,
    EdgeCaptureCandidate,
    MediaKind,
)


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 256 * 1024
MAX_URL_CHARS = 16 * 1024
MAX_TITLE_CHARS = 512
ALLOWED_METHODS = {"GET", "HEAD"}
ALLOWED_HEADER_NAMES = {
    "referer": "Referer",
    "origin": "Origin",
    "user-agent": "User-Agent",
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "range": "Range",
}
SENSITIVE_HEADER_NAMES = {"cookie", "authorization", "proxy-authorization"}

_KIND_FROM_PROTOCOL = {
    "hls": MediaKind.HLS,
    "direct_mp4": MediaKind.DIRECT_MP4,
    "dash": MediaKind.DASH,
}
_KIND_TO_PROTOCOL = {value: key for key, value in _KIND_FROM_PROTOCOL.items()}


class EdgeProtocolError(ValueError):
    """Represent a rejected Edge companion protocol message."""

    def __init__(self, code: str, message: str):
        """Create an error with a stable machine-readable code."""
        super().__init__(message)
        self.code = code


def _require_http_url(value: object, field_name: str) -> str:
    """Return an HTTP(S) URL or reject the named field."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS:
        raise EdgeProtocolError("INVALID_URL", f"{field_name} 不是有效 URL")
    if any(
        character.isspace()
        or ord(character) < 0x20
        or ord(character) == 0x7F
        for character in value
    ):
        raise EdgeProtocolError("INVALID_URL", f"{field_name} 不是有效 URL")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise EdgeProtocolError("INVALID_URL", f"{field_name} 不是有效 URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise EdgeProtocolError("INVALID_URL", f"{field_name} 只允许 HTTP(S)")
    return value


def _safe_headers(raw: object) -> dict[str, str]:
    """Canonicalize allowlisted headers and reject unsafe values."""
    if not isinstance(raw, dict):
        raise EdgeProtocolError("INVALID_HEADERS", "headers 必须是对象")
    safe = {}
    for name, value in raw.items():
        lowered = str(name).lower()
        if lowered in SENSITIVE_HEADER_NAMES or lowered not in ALLOWED_HEADER_NAMES:
            raise EdgeProtocolError("INVALID_HEADERS", f"不允许的 Header: {name}")
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise EdgeProtocolError("INVALID_HEADERS", f"Header 值无效: {name}")
        safe[ALLOWED_HEADER_NAMES[lowered]] = value
    return safe


def _require_mapping(value: object, field_name: str) -> dict:
    """Return a dictionary value or reject the named field."""
    if not isinstance(value, dict):
        raise EdgeProtocolError("INVALID_MESSAGE", f"{field_name} 必须是对象")
    return value


def _require_string(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = True,
    max_chars: int | None = None,
) -> str:
    """Return a string that satisfies the requested field constraints."""
    if not isinstance(value, str) or (not allow_empty and not value):
        raise EdgeProtocolError("INVALID_MESSAGE", f"{field_name} 必须是字符串")
    if max_chars is not None and len(value) > max_chars:
        raise EdgeProtocolError("INVALID_MESSAGE", f"{field_name} 长度超限")
    return value


def _parse_captured_at(value: object) -> datetime:
    """Parse an offset-aware timestamp and normalize it to UTC."""
    raw = _require_string(value, "captured_at", allow_empty=False)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        captured_at = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EdgeProtocolError("INVALID_TIMESTAMP", "captured_at 格式无效") from exc
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise EdgeProtocolError("INVALID_TIMESTAMP", "captured_at 必须包含时区")
    try:
        captured_at_utc = captured_at.astimezone(timezone.utc)
        _ = captured_at_utc + timedelta(seconds=EDGE_CAPTURE_TTL_SECONDS)
    except (OverflowError, ValueError) as exc:
        raise EdgeProtocolError("INVALID_TIMESTAMP", "captured_at 超出有效范围") from exc
    return captured_at_utc


def _mapping_size(raw: dict) -> int:
    """Return the UTF-8 JSON size of a candidate message mapping."""
    try:
        encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise EdgeProtocolError("INVALID_MESSAGE", "消息包含不可序列化字段") from exc
    return len(encoded)


def _parse_candidate_mapping(raw: object) -> EdgeCaptureCandidate:
    """Validate an untrusted mapping and return its candidate model."""
    message = _require_mapping(raw, "message")
    if _mapping_size(message) > MAX_MESSAGE_BYTES:
        raise EdgeProtocolError("MESSAGE_TOO_LARGE", "消息超过大小限制")
    if type(message.get("protocol_version")) is not int:
        raise EdgeProtocolError("INVALID_VERSION", "protocol_version 必须是整数")
    if message["protocol_version"] != PROTOCOL_VERSION:
        raise EdgeProtocolError("INVALID_VERSION", "不支持的协议版本")
    if message.get("type") != "media_candidate":
        raise EdgeProtocolError("INVALID_TYPE", "不支持的消息类型")
    if message.get("sensitive_headers_included") is not False:
        raise EdgeProtocolError("SENSITIVE_HEADERS", "消息不得包含敏感 Header")

    request_id = _require_string(
        message.get("request_id"), "request_id", allow_empty=False
    )
    captured_at = _parse_captured_at(message.get("captured_at"))
    page = _require_mapping(message.get("page"), "page")
    candidate = _require_mapping(message.get("candidate"), "candidate")

    page_url = _require_http_url(page.get("url"), "page.url")
    page_title = _require_string(
        page.get("title"), "page.title", max_chars=MAX_TITLE_CHARS
    )
    media_url = _require_http_url(candidate.get("url"), "candidate.url")

    raw_kind = candidate.get("kind")
    if not isinstance(raw_kind, str) or raw_kind not in _KIND_FROM_PROTOCOL:
        raise EdgeProtocolError("INVALID_KIND", "candidate.kind 不受支持")
    kind = _KIND_FROM_PROTOCOL[raw_kind]

    content_type = _require_string(
        candidate.get("content_type"), "candidate.content_type"
    )
    method = _require_string(
        candidate.get("method"), "candidate.method", allow_empty=False
    )
    if method not in ALLOWED_METHODS:
        raise EdgeProtocolError("INVALID_METHOD", "candidate.method 不受支持")
    headers = _safe_headers(candidate.get("headers"))

    return EdgeCaptureCandidate(
        request_id=request_id,
        captured_at=captured_at,
        page_url=page_url,
        page_title=page_title,
        media_url=media_url,
        kind=kind,
        content_type=content_type,
        method=method,
        headers=headers,
        protocol_version=message["protocol_version"],
    )


def _format_timestamp(value: object) -> object:
    """Format a UTC datetime with the protocol's trailing-Z spelling."""
    if not isinstance(value, datetime):
        return value
    serialized = value.isoformat()
    return serialized[:-6] + "Z" if serialized.endswith("+00:00") else serialized


def _candidate_to_mapping(candidate: EdgeCaptureCandidate) -> dict:
    """Convert a candidate model into the V1 wire mapping shape."""
    if not isinstance(candidate, EdgeCaptureCandidate):
        raise EdgeProtocolError("INVALID_CANDIDATE", "candidate 类型无效")
    return {
        "protocol_version": candidate.protocol_version,
        "type": "media_candidate",
        "request_id": candidate.request_id,
        "captured_at": _format_timestamp(candidate.captured_at),
        "page": {
            "url": candidate.page_url,
            "title": candidate.page_title,
        },
        "candidate": {
            "url": candidate.media_url,
            "kind": _KIND_TO_PROTOCOL.get(candidate.kind),
            "content_type": candidate.content_type,
            "method": candidate.method,
            "headers": dict(candidate.headers),
        },
        "sensitive_headers_included": False,
    }


def parse_candidate_json(raw_json: str) -> EdgeCaptureCandidate:
    """Parse and validate an untrusted V1 JSON message."""
    if not isinstance(raw_json, str):
        raise EdgeProtocolError("INVALID_JSON", "消息必须是 JSON 字符串")
    try:
        message_bytes = raw_json.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EdgeProtocolError("INVALID_JSON", "消息不是有效 UTF-8 文本") from exc
    if len(message_bytes) > MAX_MESSAGE_BYTES:
        raise EdgeProtocolError("MESSAGE_TOO_LARGE", "消息超过大小限制")
    try:
        raw = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError, RecursionError) as exc:
        raise EdgeProtocolError("INVALID_JSON", "消息不是有效 JSON") from exc
    return _parse_candidate_mapping(raw)


def serialize_candidate(candidate: EdgeCaptureCandidate) -> dict:
    """Validate and serialize a candidate into a V1 task payload."""
    validated = _parse_candidate_mapping(_candidate_to_mapping(candidate))
    return _candidate_to_mapping(validated)


def candidate_from_task_payload(payload: object) -> EdgeCaptureCandidate:
    """Revalidate a task payload and restore its candidate model."""
    return _parse_candidate_mapping(payload)
