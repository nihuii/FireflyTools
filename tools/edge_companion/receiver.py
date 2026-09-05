"""Run the authenticated loopback receiver for Edge capture candidates."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from tools.edge_companion.protocol import (
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    EdgeProtocolError,
    parse_candidate_json,
)
from tools.edge_companion.runtime import (
    RUNTIME_TTL_SECONDS,
    RuntimeDescriptor,
    default_runtime_path,
    remove_runtime_descriptor_if_token,
    write_runtime_descriptor,
)


_DRAIN_TIMEOUT_SECONDS = 0.25
_MAX_DRAIN_BYTES = MAX_MESSAGE_BYTES + 1
_BODY_READ_TIMEOUT_SECONDS = 2.0
_DRAIN_CHUNK_BYTES = 64 * 1024


class EdgeCaptureReceiver(QObject):
    """Receive authenticated media candidates from the local Edge companion."""

    candidate_received = pyqtSignal(object)
    status_changed = pyqtSignal(str, str)

    def __init__(
        self,
        runtime_path: Path | None = None,
        *,
        token_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        renewal_interval_seconds: float = RUNTIME_TTL_SECONDS / 2,
        parent: QObject | None = None,
    ) -> None:
        """Create a stopped receiver with injectable time and token sources."""
        super().__init__(parent)
        self._runtime_path = (
            Path(runtime_path) if runtime_path is not None else default_runtime_path()
        )
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._renewal_interval_seconds = renewal_interval_seconds
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._active_condition = threading.Condition()
        self._active_connections = set()
        self._stopping = False
        self._server = None
        self._thread = None
        self._token = None
        self._last_renewal_tick = None
        self._accepting = False
        self._auth_failure_count = 0
        self._auth_blocked_until = 0.0

    @property
    def server_address(self) -> tuple[str, int] | None:
        """Return the bound address while the receiver is running."""
        with self._lifecycle_lock:
            if self._server is None:
                return None
            host, port = self._server.server_address[:2]
            return str(host), int(port)

    def start(self) -> None:
        """Start one loopback server and publish its runtime descriptor."""
        with self._lifecycle_lock:
            if self._server is not None:
                return

            token = self._token_factory()
            if not isinstance(token, str) or not token:
                raise RuntimeError("接收器 token 生成失败")
            current = self._now()
            if (
                not isinstance(current, datetime)
                or current.tzinfo is None
                or current.utcoffset() is None
            ):
                raise RuntimeError("接收器当前时间必须包含时区")
            current = current.astimezone(timezone.utc)

            server = self._make_server()
            server.daemon_threads = True
            port = int(server.server_address[1])
            descriptor = RuntimeDescriptor(
                port=port,
                token=token,
                pid=os.getpid(),
                protocol_version=PROTOCOL_VERSION,
                expires_at=current + timedelta(seconds=RUNTIME_TTL_SECONDS),
            )
            try:
                write_runtime_descriptor(self._runtime_path, descriptor)
            except Exception:
                server.server_close()
                raise

            thread = threading.Thread(
                target=server.serve_forever,
                name="edge-capture-receiver",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            self._token = token
            self._last_renewal_tick = self._clock()
            with self._active_condition:
                self._stopping = False
            with self._state_lock:
                self._accepting = False
                self._auth_failure_count = 0
                self._auth_blocked_until = 0.0
            try:
                thread.start()
            except Exception:
                self._server = None
                self._thread = None
                self._token = None
                self._last_renewal_tick = None
                server.server_close()
                try:
                    remove_runtime_descriptor_if_token(self._runtime_path, token)
                except RuntimeError:
                    pass
                raise

        self.status_changed.emit("未连接", "接收器已启动，等待用户授权捕获。")

    def set_accepting(self, accepting: bool) -> None:
        """Enable or disable delivery of validated capture candidates."""
        with self._state_lock:
            self._accepting = bool(accepting)
        self.status_changed.emit("等待捕获" if accepting else "未连接", "")

    def stop(self) -> None:
        """Stop the server and remove only this instance's descriptor."""
        with self._lifecycle_lock:
            if self._server is None:
                return
            deadline = time.monotonic() + 2.0
            server = self._server
            thread = self._thread
            token = self._token
            with self._active_condition:
                self._stopping = True
            try:
                server.shutdown()
            finally:
                server.server_close()
                self._interrupt_active_connections()
                self._wait_for_active_connections(deadline)
                if thread is not None:
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
                if token is not None:
                    try:
                        remove_runtime_descriptor_if_token(self._runtime_path, token)
                    except RuntimeError:
                        pass
                self._server = None
                self._thread = None
                self._token = None
                self._last_renewal_tick = None
                with self._state_lock:
                    self._accepting = False
                    self._auth_failure_count = 0
                    self._auth_blocked_until = 0.0

    @staticmethod
    def _close_connection(connection) -> None:
        """Best-effort close one active request socket from the stop thread."""
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            connection.close()
        except OSError:
            pass

    def _register_active_connection(self, connection) -> bool:
        """Track a handler socket and reject it immediately while stopping."""
        with self._active_condition:
            self._active_connections.add(connection)
            stopping = self._stopping
            self._active_condition.notify_all()
        if stopping:
            self._close_connection(connection)
        return not stopping

    def _unregister_active_connection(self, connection) -> None:
        """Forget a finished handler socket and wake a waiting stop call."""
        with self._active_condition:
            self._active_connections.discard(connection)
            self._active_condition.notify_all()

    def _interrupt_active_connections(self) -> None:
        """Close a snapshot of active handler sockets to interrupt body reads."""
        with self._active_condition:
            connections = tuple(self._active_connections)
        for connection in connections:
            self._close_connection(connection)

    def _wait_for_active_connections(self, deadline: float) -> None:
        """Wait within ``deadline`` for all registered handlers to finish."""
        with self._active_condition:
            while self._active_connections:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._active_condition.wait(timeout=remaining)

    @staticmethod
    def _is_loopback_client(client_address) -> bool:
        """Return whether a handler client address is a loopback address."""
        try:
            host = client_address[0]
            return ipaddress.ip_address(host).is_loopback
        except (IndexError, TypeError, ValueError):
            return False

    def _make_handler(self):
        """Build the request-handler class bound to this receiver instance."""
        receiver = self

        class EdgeCaptureRequestHandler(BaseHTTPRequestHandler):
            """Translate HTTP requests into receiver operations."""

            def setup(self):
                """Create streams and register this handler's active socket."""
                self._receiver_registered = False
                self._receiver_should_handle = False
                super().setup()
                self._receiver_registered = True
                self._receiver_should_handle = receiver._register_active_connection(
                    self.connection
                )

            def handle(self):
                """Handle requests only when registration predates stopping."""
                if self._receiver_should_handle:
                    super().handle()

            def finish(self):
                """Close streams and unregister this handler even after errors."""
                try:
                    super().finish()
                finally:
                    if self._receiver_registered:
                        receiver._unregister_active_connection(self.connection)

            def do_POST(self):
                """Process one candidate submission."""
                receiver._handle_post(self)

            def _reject_method(self):
                """Reject a non-POST request with a structured response."""
                receiver._send_json(self, 405, "METHOD_NOT_ALLOWED")

            do_GET = _reject_method
            do_HEAD = _reject_method
            do_PUT = _reject_method
            do_PATCH = _reject_method
            do_DELETE = _reject_method
            do_OPTIONS = _reject_method

            def log_message(self, _format, *_args):
                """Suppress request logs so credentials and payloads cannot leak."""
                return

        return EdgeCaptureRequestHandler

    def _make_server(self) -> ThreadingHTTPServer:
        """Build a loopback server that renews the descriptor while serving."""
        receiver = self

        class EdgeCaptureHTTPServer(ThreadingHTTPServer):
            def service_actions(self):
                receiver._renew_runtime_descriptor(self)

        return EdgeCaptureHTTPServer(("127.0.0.1", 0), self._make_handler())

    def _renew_runtime_descriptor(self, server: ThreadingHTTPServer) -> None:
        """Extend this running server's short-lived discovery lease when due."""
        with self._active_condition:
            if self._stopping:
                return
        if server is not self._server or self._token is None:
            return
        current_tick = self._clock()
        if (
            self._last_renewal_tick is not None
            and current_tick - self._last_renewal_tick
            < self._renewal_interval_seconds
        ):
            return
        self._last_renewal_tick = current_tick
        try:
            current = self._now()
            if (
                not isinstance(current, datetime)
                or current.tzinfo is None
                or current.utcoffset() is None
            ):
                raise ValueError("receiver clock must include a timezone")
            descriptor = RuntimeDescriptor(
                port=int(server.server_address[1]),
                token=self._token,
                pid=os.getpid(),
                protocol_version=PROTOCOL_VERSION,
                expires_at=current.astimezone(timezone.utc)
                + timedelta(seconds=RUNTIME_TTL_SECONDS),
            )
            write_runtime_descriptor(self._runtime_path, descriptor)
        except Exception:
            self.status_changed.emit("错误", "无法续租 Edge 捕获连接，请重启应用。")

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        """Validate and deliver one authenticated POST request."""
        if not self._is_loopback_client(handler.client_address):
            self._send_json(handler, 403, "FORBIDDEN")
            self._drain_request_body(handler)
            return

        with self._state_lock:
            current_tick = self._clock()
            if current_tick < self._auth_blocked_until:
                self._send_json(handler, 429, "AUTH_RATE_LIMITED")
                self._drain_request_body(handler)
                return

        authorization_headers = handler.headers.get_all("Authorization", [])
        supplied_authorization = (
            authorization_headers[0] if len(authorization_headers) == 1 else ""
        )
        expected_authorization = f"Bearer {self._token}"
        if not hmac.compare_digest(
            supplied_authorization.encode("utf-8"),
            expected_authorization.encode("utf-8"),
        ):
            with self._state_lock:
                self._auth_failure_count += 1
                if self._auth_failure_count >= 3:
                    blocked_seconds = min(
                        2 ** (self._auth_failure_count - 3), 30
                    )
                    self._auth_blocked_until = current_tick + blocked_seconds
            self._send_json(handler, 401, "UNAUTHORIZED")
            self._drain_request_body(handler)
            return

        with self._state_lock:
            self._auth_failure_count = 0
            self._auth_blocked_until = 0.0

        content_length_headers = handler.headers.get_all("Content-Length", [])
        if len(content_length_headers) != 1:
            self._send_json(handler, 400, "INVALID_CONTENT_LENGTH")
            return
        content_length_text = content_length_headers[0]
        if not content_length_text.isascii() or not content_length_text.isdigit():
            self._send_json(handler, 400, "INVALID_CONTENT_LENGTH")
            return
        content_length = int(content_length_text)
        if content_length > MAX_MESSAGE_BYTES:
            self._send_json(handler, 413, "MESSAGE_TOO_LARGE")
            self._drain_request_body(handler, content_length)
            return

        content_type_headers = handler.headers.get_all("Content-Type", [])
        if len(content_type_headers) != 1 or (
            content_type_headers[0].split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            self._send_json(handler, 415, "UNSUPPORTED_MEDIA_TYPE")
            self._drain_request_body(handler, content_length)
            return

        previous_timeout = handler.connection.gettimeout()
        try:
            handler.connection.settimeout(_BODY_READ_TIMEOUT_SECONDS)
            raw_message = handler.rfile.read(content_length)
        except (OSError, TimeoutError):
            self._send_json(handler, 400, "INVALID_JSON")
            return
        finally:
            try:
                handler.connection.settimeout(previous_timeout)
            except OSError:
                pass
        if len(raw_message) != content_length:
            self._send_json(handler, 400, "INVALID_JSON")
            return
        try:
            message_text = raw_message.decode("utf-8")
        except UnicodeDecodeError:
            self._send_json(handler, 400, "INVALID_JSON")
            return
        try:
            candidate = parse_candidate_json(message_text)
        except EdgeProtocolError as exc:
            self._send_json(handler, 400, exc.code)
            return

        with self._state_lock:
            accepting = self._accepting
        if not accepting:
            self._send_json(handler, 409, "APP_NOT_WAITING")
            return

        self.candidate_received.emit(candidate)
        self._send_json(handler, 202, "ACCEPTED")

    @staticmethod
    def _drain_request_body(
        handler: BaseHTTPRequestHandler,
        content_length: int | None = None,
    ) -> bool:
        """Discard a known request body in chunks without decoding or retaining it."""
        if content_length is None:
            content_lengths = handler.headers.get_all("Content-Length", [])
            if len(content_lengths) != 1:
                return False
            raw_length = content_lengths[0]
            if not raw_length.isascii() or not raw_length.isdigit():
                return False
            content_length = int(raw_length)

        if content_length < 0:
            return False

        remaining = min(content_length, _MAX_DRAIN_BYTES)
        deadline = time.monotonic() + _DRAIN_TIMEOUT_SECONDS
        previous_timeout = handler.connection.gettimeout()
        try:
            while remaining:
                time_left = deadline - time.monotonic()
                if time_left <= 0:
                    return False
                handler.connection.settimeout(time_left)
                chunk = handler.rfile.read(min(_DRAIN_CHUNK_BYTES, remaining))
                if not chunk:
                    return False
                remaining -= len(chunk)
            return content_length <= _MAX_DRAIN_BYTES
        except (OSError, TimeoutError):
            return False
        finally:
            try:
                handler.connection.settimeout(previous_timeout)
            except OSError:
                pass

    @staticmethod
    def _send_json(
        handler: BaseHTTPRequestHandler,
        status: int,
        code: str,
    ) -> None:
        """Send a structured JSON status without reflecting request values."""
        body = json.dumps(
            {"code": code}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            if handler.command != "HEAD":
                handler.wfile.write(body)
            handler.wfile.flush()
            handler.close_connection = True
        except OSError:
            pass
