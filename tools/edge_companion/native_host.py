"""Bridge Edge Native Messaging frames to the local capture receiver."""

import json
import re
import struct
import sys
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

from tools.edge_companion.protocol import (
    MAX_MESSAGE_BYTES,
    EdgeProtocolError,
    parse_candidate_json,
    serialize_candidate,
)
from tools.edge_companion.runtime import (
    default_runtime_path,
    pid_is_alive,
    read_runtime_descriptor,
)
from tools.video_crawler.logging_utils import redact_for_display


ALLOWED_EXTENSION_ORIGIN = (
    "chrome-extension://applbmkghgaoadhmmcdnbmebgideiefg/"
)
_SAFE_UNVALIDATED_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,256}\Z")
_RECEIVER_STATUS = {
    202: (True, "ACCEPTED"),
    401: (False, "UNAUTHORIZED"),
    403: (False, "FORBIDDEN"),
    409: (False, "APP_NOT_WAITING"),
    413: (False, "MESSAGE_TOO_LARGE"),
    415: (False, "UNSUPPORTED_MEDIA_TYPE"),
    429: (False, "AUTH_RATE_LIMITED"),
}


def read_native_message(stream) -> dict | None:
    """Read one size-limited Native Messaging JSON object from ``stream``."""
    prefix = stream.read(4)
    if prefix == b"":
        return None
    if len(prefix) != 4:
        raise EdgeProtocolError("INVALID_FRAME", "Native Messaging 长度前缀不完整")
    length = struct.unpack("=I", prefix)[0]
    if length > MAX_MESSAGE_BYTES:
        raise EdgeProtocolError("MESSAGE_TOO_LARGE", "Native Messaging 消息过大")
    body = stream.read(length)
    if len(body) != length:
        raise EdgeProtocolError("INVALID_FRAME", "Native Messaging 消息体不完整")
    try:
        decoded = body.decode("utf-8")
        message = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise EdgeProtocolError("INVALID_JSON", "Native Messaging JSON 无效") from exc
    if not isinstance(message, dict):
        raise EdgeProtocolError("INVALID_MESSAGE", "Native Messaging 消息必须是对象")
    return message


def write_native_message(stream, message: dict) -> None:
    """Write and flush one size-limited Native Messaging JSON object."""
    if not isinstance(message, dict):
        raise EdgeProtocolError("INVALID_MESSAGE", "Native Messaging 响应必须是对象")
    try:
        serialized = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise EdgeProtocolError("INVALID_MESSAGE", "Native Messaging 响应不可序列化") from exc
    try:
        body = serialized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EdgeProtocolError("INVALID_JSON", "Native Messaging 响应不是有效 UTF-8") from exc
    if len(body) > MAX_MESSAGE_BYTES:
        raise EdgeProtocolError("MESSAGE_TOO_LARGE", "Native Messaging 响应过大")
    stream.write(struct.pack("=I", len(body)))
    stream.write(body)
    stream.flush()


def _diagnose(stderr, message: object, *, secrets=()) -> None:
    """Write a redacted diagnostic line without touching protocol stdout."""
    diagnostic = "" if message is None else str(message)
    for secret in secrets:
        if isinstance(secret, str) and secret:
            diagnostic = diagnostic.replace(secret, "<redacted>")
    stderr.write(redact_for_display(diagnostic) + "\n")
    stderr.flush()


def _unvalidated_request_id(message: dict) -> str:
    """Return only a conservative correlation ID before protocol validation."""
    request_id = message.get("request_id")
    if isinstance(request_id, str) and _SAFE_UNVALIDATED_REQUEST_ID.fullmatch(
        request_id
    ):
        return request_id
    return ""


def _ack(request_id: str, ok: bool, code: str) -> dict:
    """Build the only response shape exposed to the extension."""
    return {
        "type": "ack",
        "request_id": request_id,
        "ok": ok,
        "code": code,
    }


def _receiver_result(status: int) -> tuple[bool, str]:
    """Map an HTTP status without reading or reflecting its response body."""
    return _RECEIVER_STATUS.get(status, (False, "RECEIVER_ERROR"))


def _forward_candidate(
    message: dict,
    *,
    runtime_path: Path | None,
    descriptor_reader,
    pid_checker,
    urlopen,
    timeout: float,
    stderr,
) -> tuple[str, bool, str]:
    """Validate and forward one candidate, returning correlation and ack fields."""
    raw_json = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    candidate = parse_candidate_json(raw_json)
    request_id = candidate.request_id
    payload = serialize_candidate(candidate)
    body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    try:
        descriptor_path = runtime_path or default_runtime_path()
        descriptor = descriptor_reader(
            descriptor_path,
            pid_checker=pid_checker,
        )
    except Exception:
        _diagnose(stderr, "APP_NOT_RUNNING")
        return request_id, False, "APP_NOT_RUNNING"

    request = urllib.request.Request(
        f"http://127.0.0.1:{descriptor.port}/v1/candidate",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {descriptor.token}",
        },
        method="POST",
    )
    response = None
    try:
        response = urlopen(request, timeout=timeout)
        status = getattr(response, "status", None)
        if status is None:
            status = response.getcode()
        return (request_id, *_receiver_result(status))
    except HTTPError as exc:
        return (request_id, *_receiver_result(exc.code))
    except (URLError, OSError) as exc:
        _diagnose(
            stderr,
            f"APP_NOT_RUNNING: {exc}",
            secrets=(descriptor.token,),
        )
        return request_id, False, "APP_NOT_RUNNING"
    except Exception as exc:
        _diagnose(
            stderr,
            f"RECEIVER_ERROR: {exc}",
            secrets=(descriptor.token,),
        )
        return request_id, False, "RECEIVER_ERROR"
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    _diagnose(
                        stderr,
                        f"RESPONSE_CLOSE_ERROR: {exc}",
                        secrets=(descriptor.token,),
                    )


def run_host(
    caller_origin: str,
    *,
    stdin=None,
    stdout=None,
    stderr=None,
    runtime_path: Path | None = None,
    descriptor_reader=None,
    pid_checker=None,
    urlopen=None,
    timeout: float = 1.0,
) -> int:
    """Process Native Messaging frames until EOF and acknowledge each frame."""
    input_stream = stdin if stdin is not None else sys.stdin.buffer
    output_stream = stdout if stdout is not None else sys.stdout.buffer
    diagnostic_stream = stderr if stderr is not None else sys.stderr
    load_descriptor = descriptor_reader or read_runtime_descriptor
    check_pid = pid_checker or pid_is_alive
    open_url = urlopen or urllib.request.urlopen

    while True:
        try:
            message = read_native_message(input_stream)
        except EdgeProtocolError as exc:
            _diagnose(diagnostic_stream, exc.code)
            try:
                write_native_message(output_stream, _ack("", False, exc.code))
            except (EdgeProtocolError, OSError) as write_error:
                _diagnose(diagnostic_stream, f"WRITE_ERROR: {write_error}")
                return 1
            if exc.code in {"INVALID_FRAME", "MESSAGE_TOO_LARGE"}:
                return 1
            continue

        if message is None:
            return 0

        request_id = _unvalidated_request_id(message)
        if caller_origin != ALLOWED_EXTENSION_ORIGIN:
            response = _ack(request_id, False, "ORIGIN_NOT_ALLOWED")
        else:
            try:
                request_id, ok, code = _forward_candidate(
                    message,
                    runtime_path=runtime_path,
                    descriptor_reader=load_descriptor,
                    pid_checker=check_pid,
                    urlopen=open_url,
                    timeout=timeout,
                    stderr=diagnostic_stream,
                )
                response = _ack(request_id, ok, code)
            except EdgeProtocolError as exc:
                _diagnose(diagnostic_stream, exc.code)
                response = _ack(request_id, False, exc.code)
            except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
                _diagnose(diagnostic_stream, f"INVALID_MESSAGE: {exc}")
                response = _ack(request_id, False, "INVALID_MESSAGE")

        try:
            write_native_message(output_stream, response)
        except (EdgeProtocolError, OSError) as exc:
            _diagnose(diagnostic_stream, f"WRITE_ERROR: {exc}")
            return 1


def main(
    argv=None,
    *,
    stdin=None,
    stdout=None,
    stderr=None,
    runtime_path: Path | None = None,
) -> int:
    """Validate Edge launch arguments and run the binary protocol loop."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    diagnostic_stream = stderr if stderr is not None else sys.stderr
    if (
        not arguments
        or len(arguments) > 2
        or (
            len(arguments) == 2
            and not arguments[1].startswith("--parent-window=")
        )
    ):
        _diagnose(diagnostic_stream, "INVALID_ARGUMENTS")
        return 2
    return run_host(
        arguments[0],
        stdin=stdin,
        stdout=stdout,
        stderr=diagnostic_stream,
        runtime_path=runtime_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
