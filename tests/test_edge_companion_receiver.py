from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from dataclasses import replace
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

from PyQt6.QtCore import Qt

from tests.edge_companion_fixtures import valid_edge_message
import tools.edge_companion.runtime as runtime_module
from tools.edge_companion.receiver import EdgeCaptureReceiver
from tools.edge_companion.runtime import (
    RUNTIME_TTL_SECONDS,
    RuntimeDescriptor,
    default_runtime_path,
    pid_is_alive,
    read_runtime_descriptor,
    write_runtime_descriptor,
)
from tools.video_crawler.models import EdgeCaptureCandidate


class MutableClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@contextmanager
def temporary_directory():
    path = Path(tempfile.gettempdir()) / f"edge-receiver-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class RuntimeDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
        self.descriptor = RuntimeDescriptor(
            port=32123,
            token="test-token",
            pid=os.getpid(),
            protocol_version=1,
            expires_at=self.now + timedelta(seconds=RUNTIME_TTL_SECONDS),
        )

    def test_descriptor_round_trips_with_atomic_temporary_name(self):
        with temporary_directory() as temporary_path:
            runtime_path = temporary_path / "nested" / "edge_capture.json"

            write_runtime_descriptor(runtime_path, self.descriptor)

            self.assertTrue(runtime_path.exists())
            self.assertFalse(
                runtime_path.with_name(runtime_path.name + ".tmp").exists()
            )
            raw = json.loads(runtime_path.read_text(encoding="utf-8"))
            self.assertEqual(raw["expires_at"], "2026-09-03T08:10:00Z")
            restored = read_runtime_descriptor(
                runtime_path,
                now=lambda: self.now,
                pid_checker=lambda pid: pid == os.getpid(),
            )
            self.assertEqual(restored, self.descriptor)

    def test_default_runtime_path_requires_localappdata(self):
        with temporary_directory() as temporary_path:
            with mock.patch.dict(
                os.environ, {"LOCALAPPDATA": str(temporary_path)}, clear=True
            ):
                self.assertEqual(
                    default_runtime_path(),
                    temporary_path
                    / "FireflyTools"
                    / "runtime"
                    / "edge_capture.json",
                )
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(RuntimeError):
                    default_runtime_path()

    def test_reader_rejects_invalid_stale_and_dead_descriptors(self):
        valid = self.descriptor.to_dict()
        invalid_cases = (
            ("extra field", {**valid, "extra": True}, True),
            (
                "missing field",
                {key: value for key, value in valid.items() if key != "token"},
                True,
            ),
            ("boolean port", {**valid, "port": True}, True),
            ("port range", {**valid, "port": 0}, True),
            ("empty token", {**valid, "token": ""}, True),
            ("boolean pid", {**valid, "pid": True}, True),
            ("wrong version", {**valid, "protocol_version": 2}, True),
            (
                "naive expiry",
                {**valid, "expires_at": "2026-09-03T08:10:00"},
                True,
            ),
            (
                "non-UTC expiry",
                {**valid, "expires_at": "2026-09-03T16:10:00+08:00"},
                True,
            ),
            (
                "expired",
                {**valid, "expires_at": "2026-09-03T08:00:00Z"},
                True,
            ),
            ("dead process", valid, False),
        )

        with temporary_directory() as temporary_path:
            runtime_path = temporary_path / "edge_capture.json"
            for label, raw, process_alive in invalid_cases:
                with self.subTest(label=label):
                    runtime_path.write_text(json.dumps(raw), encoding="utf-8")
                    with self.assertRaises(ValueError) as caught:
                        read_runtime_descriptor(
                            runtime_path,
                            now=lambda: self.now,
                            pid_checker=lambda _pid, alive=process_alive: alive,
                        )
                    self.assertNotIn("test-token", str(caught.exception))

    def test_pid_liveness_rejects_non_positive_pid_and_accepts_current_process(self):
        self.assertFalse(pid_is_alive(0))
        self.assertFalse(pid_is_alive(-1))
        self.assertTrue(pid_is_alive(os.getpid()))

    def test_reader_wraps_invalid_utf8_without_exposing_file_bytes(self):
        with temporary_directory() as temporary_path:
            runtime_path = temporary_path / "edge_capture.json"
            runtime_path.write_bytes(b'\xff"test-token"')

            with self.assertRaises(ValueError) as caught:
                read_runtime_descriptor(runtime_path, now=lambda: self.now)

            self.assertIs(type(caught.exception), ValueError)
            self.assertNotIn("test-token", str(caught.exception))

    def test_remove_old_token_cannot_delete_a_concurrent_new_descriptor(self):
        with temporary_directory() as temporary_path:
            runtime_path = temporary_path / "edge_capture.json"
            old_descriptor = replace(self.descriptor, token="old-token")
            new_descriptor = replace(self.descriptor, token="new-token")
            write_runtime_descriptor(runtime_path, old_descriptor)
            writer_at_replace = threading.Event()
            release_writer = threading.Event()
            remover_started = threading.Event()
            remover_finished = threading.Event()
            errors = []
            remove_results = []
            real_replace = runtime_module.os.replace

            def controlled_replace(source, destination):
                if threading.current_thread().name == "new-descriptor-writer":
                    writer_at_replace.set()
                    if not release_writer.wait(1):
                        raise TimeoutError("writer release timed out")
                return real_replace(source, destination)

            def write_new_descriptor():
                try:
                    write_runtime_descriptor(runtime_path, new_descriptor)
                except Exception as exc:
                    errors.append(exc)

            def remove_old_descriptor():
                remover_started.set()
                try:
                    remove_results.append(
                        runtime_module.remove_runtime_descriptor_if_token(
                            runtime_path, "old-token"
                        )
                    )
                except Exception as exc:
                    errors.append(exc)
                finally:
                    remover_finished.set()

            with mock.patch.object(
                runtime_module.os, "replace", side_effect=controlled_replace
            ):
                writer = threading.Thread(
                    target=write_new_descriptor,
                    name="new-descriptor-writer",
                )
                writer.start()
                self.assertTrue(writer_at_replace.wait(1))
                remover = threading.Thread(
                    target=remove_old_descriptor,
                    name="old-descriptor-remover",
                )
                remover.start()
                self.assertTrue(remover_started.wait(1))
                self.assertFalse(remover_finished.wait(0.05))
                release_writer.set()
                writer.join(2)
                remover.join(2)

            self.assertFalse(writer.is_alive())
            self.assertFalse(remover.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(remove_results, [False])
            self.assertEqual(
                read_runtime_descriptor(
                    runtime_path,
                    now=lambda: self.now,
                    pid_checker=lambda _pid: True,
                ).token,
                "new-token",
            )

    def test_concurrent_writers_use_separate_temporary_files(self):
        with temporary_directory() as temporary_path:
            runtime_path = temporary_path / "edge_capture.json"
            descriptors = tuple(
                replace(self.descriptor, token=f"writer-{index}")
                for index in range(2)
            )
            start_writers = threading.Event()
            replace_sources = []
            replace_sources_lock = threading.Lock()
            errors = []
            real_replace = runtime_module.os.replace

            def recording_replace(source, destination):
                with replace_sources_lock:
                    replace_sources.append(Path(source).name)
                return real_replace(source, destination)

            def write_descriptor(descriptor):
                start_writers.wait(1)
                try:
                    write_runtime_descriptor(runtime_path, descriptor)
                except Exception as exc:
                    errors.append(exc)

            with mock.patch.object(
                runtime_module.os, "replace", side_effect=recording_replace
            ):
                writers = [
                    threading.Thread(target=write_descriptor, args=(descriptor,))
                    for descriptor in descriptors
                ]
                for writer in writers:
                    writer.start()
                start_writers.set()
                for writer in writers:
                    writer.join(2)

            self.assertTrue(all(not writer.is_alive() for writer in writers))
            self.assertEqual(
                errors,
                [],
                [f"{error!r} caused by {error.__cause__!r}" for error in errors],
            )
            self.assertEqual(len(replace_sources), 2)
            self.assertEqual(len(set(replace_sources)), 2)
            for source_name in replace_sources:
                self.assertTrue(source_name.startswith(runtime_path.name + "."))
                self.assertTrue(source_name.endswith(".tmp"))
            self.assertEqual(
                list(temporary_path.glob(runtime_path.name + ".*.tmp")), []
            )
            self.assertIn(
                read_runtime_descriptor(
                    runtime_path,
                    now=lambda: self.now,
                    pid_checker=lambda _pid: True,
                ).token,
                {descriptor.token for descriptor in descriptors},
            )


class EdgeCaptureReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = (
            Path(tempfile.gettempdir())
            / f"edge-receiver-test-{uuid.uuid4().hex}"
        )
        self.temporary_directory.mkdir()
        self.runtime_path = (
            self.temporary_directory / "runtime" / "edge_capture.json"
        )
        self.now = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
        self.clock = MutableClock()
        self.receivers = []

    def tearDown(self):
        for receiver in reversed(self.receivers):
            receiver.stop()
        shutil.rmtree(self.temporary_directory, ignore_errors=True)

    def make_receiver(self, **overrides):
        options = {
            "runtime_path": self.runtime_path,
            "token_factory": lambda: "test-token",
            "clock": self.clock,
            "now": lambda: self.now,
        }
        options.update(overrides)
        receiver = EdgeCaptureReceiver(**options)
        self.receivers.append(receiver)
        return receiver

    def request(self, receiver, *, body=b"{}", headers=None, method="POST"):
        request_headers = dict(headers or {})
        connection = http.client.HTTPConnection(
            receiver.server_address[0], receiver.server_address[1], timeout=2
        )
        try:
            connection.request(method, "/", body=body, headers=request_headers)
            response = connection.getresponse()
            response_body = response.read()
            parsed = json.loads(response_body.decode("utf-8")) if response_body else {}
            return response.status, response.getheader("Content-Type"), parsed
        finally:
            connection.close()

    def authorized_headers(self, **extra):
        return {
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
            **extra,
        }

    def test_start_binds_loopback_writes_descriptor_and_is_idempotent(self):
        tokens = iter(("test-token", "unexpected-second-token"))
        receiver = self.make_receiver(token_factory=tokens.__next__)
        statuses = []
        receiver.status_changed.connect(lambda state, detail: statuses.append((state, detail)))

        receiver.start()
        first_address = receiver.server_address
        receiver.start()

        self.assertEqual(first_address[0], "127.0.0.1")
        self.assertNotEqual(first_address[1], 0)
        self.assertEqual(receiver.server_address, first_address)
        descriptor = read_runtime_descriptor(
            self.runtime_path,
            now=lambda: self.now,
            pid_checker=lambda pid: pid == os.getpid(),
        )
        self.assertEqual(descriptor.pid, os.getpid())
        self.assertEqual(descriptor.protocol_version, 1)
        self.assertEqual(descriptor.token, "test-token")
        self.assertEqual(
            descriptor.expires_at,
            self.now + timedelta(seconds=RUNTIME_TTL_SECONDS),
        )
        self.assertFalse(
            self.runtime_path.with_name(self.runtime_path.name + ".tmp").exists()
        )
        self.assertEqual(
            statuses,
            [("未连接", "接收器已启动，等待用户授权捕获。")],
        )

    def test_missing_and_wrong_authorization_are_rejected(self):
        receiver = self.make_receiver()
        receiver.start()

        for label, authorization in (
            ("missing", None),
            ("wrong", "Bearer wrong-secret"),
            ("non-ascii", "Bearer wrong-é"),
        ):
            headers = {"Content-Type": "application/json"}
            if authorization is not None:
                headers["Authorization"] = authorization
            with self.subTest(label=label):
                status, content_type, response = self.request(
                    receiver, body=b"not-json", headers=headers
                )
                self.assertEqual(status, 401)
                self.assertEqual(content_type, "application/json")
                self.assertEqual(response["code"], "UNAUTHORIZED")

    def test_non_loopback_client_is_rejected_through_handler_seam(self):
        receiver = self.make_receiver()
        receiver.start()
        self.assertFalse(receiver._is_loopback_client(("192.0.2.1", 1234)))
        receiver._is_loopback_client = lambda _client_address: False
        payload = json.dumps(valid_edge_message()).encode("utf-8")

        for attempt in range(15):
            with self.subTest(attempt=attempt):
                status, content_type, response = self.request(
                    receiver,
                    body=payload,
                    headers=self.authorized_headers(),
                )
                self.assertEqual(status, 403)
                self.assertEqual(content_type, "application/json")
                self.assertEqual(response["code"], "FORBIDDEN")

    def test_content_checks_happen_before_json_decode(self):
        receiver = self.make_receiver()
        receiver.start()

        status, _, response = self.request(
            receiver,
            body=b"not-json",
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "text/plain",
            },
        )
        self.assertEqual(status, 415)
        self.assertEqual(response["code"], "UNSUPPORTED_MEDIA_TYPE")

        oversized_body = b"x" * (256 * 1024 + 1)
        for attempt in range(15):
            with self.subTest(attempt=attempt):
                status, content_type, response = self.request(
                    receiver,
                    body=oversized_body,
                    headers=self.authorized_headers(),
                )
                self.assertEqual(status, 413)
                self.assertEqual(content_type, "application/json")
                self.assertEqual(response["code"], "MESSAGE_TOO_LARGE")

    def test_malformed_json_returns_structured_code_without_reflection(self):
        receiver = self.make_receiver()
        receiver.start()
        raw_body = b'{"private-raw-fragment":'

        status, content_type, response = self.request(
            receiver,
            body=raw_body,
            headers=self.authorized_headers(),
        )

        self.assertEqual(status, 400)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(response["code"], "INVALID_JSON")
        response_text = json.dumps(response, ensure_ascii=False)
        self.assertNotIn("private-raw-fragment", response_text)
        self.assertNotIn("test-token", response_text)

    def test_rejected_truncated_bodies_respond_and_leave_no_handler_thread(self):
        receiver = self.make_receiver()
        receiver.start()
        existing_thread_ids = {thread.ident for thread in threading.enumerate()}
        handler_threads = set()

        for attempt, authorization, expected_status in (
            (1, None, 401),
            (2, "Bearer wrong-token", 401),
            (3, "Bearer wrong-token", 401),
            (4, "Bearer wrong-token", 429),
        ):
            with self.subTest(attempt=attempt):
                client = socket.create_connection(receiver.server_address, timeout=1)
                client.settimeout(0.75)
                headers = [
                    "POST / HTTP/1.1",
                    f"Host: {receiver.server_address[0]}",
                    "Content-Type: application/json",
                    "Content-Length: 1024",
                    "Connection: close",
                ]
                if authorization is not None:
                    headers.append(f"Authorization: {authorization}")
                request = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")
                try:
                    client.sendall(request)
                    try:
                        response = client.recv(4096)
                    except TimeoutError:
                        response = b""
                    handler_threads.update(
                        thread
                        for thread in threading.enumerate()
                        if thread.ident not in existing_thread_ids
                        and "process_request_thread" in thread.name
                    )
                finally:
                    client.close()
                self.assertIn(f" {expected_status} ".encode("ascii"), response)

        self.assertTrue(handler_threads)
        receiver.stop()
        for thread in handler_threads:
            thread.join(1)
        self.assertTrue(all(not thread.is_alive() for thread in handler_threads))

    def test_stop_interrupts_an_active_partial_authenticated_request(self):
        receiver = self.make_receiver()
        received = []
        receiver.candidate_received.connect(
            received.append, Qt.ConnectionType.DirectConnection
        )
        receiver.start()
        existing_thread_ids = {thread.ident for thread in threading.enumerate()}
        client = socket.create_connection(receiver.server_address, timeout=1)
        client.settimeout(0.5)
        request = (
            "POST / HTTP/1.1\r\n"
            f"Host: {receiver.server_address[0]}\r\n"
            "Authorization: Bearer test-token\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {64 * 1024}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii") + b"{"

        try:
            client.sendall(request)
            handler_thread = None
            deadline = time.monotonic() + 1
            poll_event = threading.Event()
            while time.monotonic() < deadline and handler_thread is None:
                frames = sys._current_frames()
                for thread in threading.enumerate():
                    if (
                        thread.ident in existing_thread_ids
                        or "process_request_thread" not in thread.name
                    ):
                        continue
                    frame = frames.get(thread.ident)
                    function_names = set()
                    while frame is not None:
                        function_names.add(frame.f_code.co_name)
                        frame = frame.f_back
                    if "_handle_post" in function_names and "readinto" in function_names:
                        handler_thread = thread
                        break
                if handler_thread is None:
                    poll_event.wait(0.01)

            self.assertIsNotNone(handler_thread)
            self.assertTrue(self.runtime_path.exists())
            stop_started = time.monotonic()

            receiver.stop()

            self.assertLessEqual(time.monotonic() - stop_started, 2.5)
            self.assertFalse(handler_thread.is_alive())
            self.assertEqual(
                getattr(receiver, "_active_connections", {object()}), set()
            )
            try:
                closed_data = client.recv(1)
                client_closed = closed_data == b""
            except TimeoutError:
                client_closed = False
            except OSError:
                client_closed = True
            self.assertTrue(client_closed)
            self.assertEqual(received, [])
            self.assertFalse(self.runtime_path.exists())
        finally:
            client.close()

    def test_valid_candidate_requires_accepting_and_emits_once(self):
        receiver = self.make_receiver()
        received = []
        received_event = threading.Event()

        def record(candidate):
            received.append(candidate)
            received_event.set()

        receiver.candidate_received.connect(record, Qt.ConnectionType.DirectConnection)
        receiver.start()
        payload = json.dumps(valid_edge_message()).encode("utf-8")

        status, _, response = self.request(
            receiver, body=payload, headers=self.authorized_headers()
        )
        self.assertEqual(status, 409)
        self.assertEqual(response["code"], "APP_NOT_WAITING")
        self.assertEqual(received, [])

        receiver.set_accepting(True)
        status, _, response = self.request(
            receiver, body=payload, headers=self.authorized_headers()
        )
        self.assertEqual(status, 202)
        self.assertEqual(response["code"], "ACCEPTED")
        self.assertTrue(received_event.wait(1))
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], EdgeCaptureCandidate)

    def test_auth_backoff_is_exponential_bounded_and_resets_after_success(self):
        receiver = self.make_receiver()
        receiver.start()
        wrong_headers = {
            "Authorization": "Bearer wrong-secret",
            "Content-Type": "application/json",
        }
        valid_payload = json.dumps(valid_edge_message()).encode("utf-8")

        for _ in range(2):
            self.assertEqual(
                self.request(receiver, headers=wrong_headers)[0],
                401,
            )

        for blocked_seconds in (1, 2, 4, 8, 16, 30, 30):
            self.assertEqual(self.request(receiver, headers=wrong_headers)[0], 401)
            self.assertEqual(
                self.request(receiver, headers=self.authorized_headers())[0], 429
            )
            self.clock.advance(blocked_seconds - 0.01)
            self.assertEqual(
                self.request(receiver, headers=self.authorized_headers())[0], 429
            )
            self.clock.advance(0.01)

        self.assertEqual(
            self.request(
                receiver,
                body=valid_payload,
                headers=self.authorized_headers(),
            )[0],
            409,
        )
        self.assertEqual(self.request(receiver, headers=wrong_headers)[0], 401)
        self.assertEqual(self.request(receiver, headers=wrong_headers)[0], 401)
        self.assertEqual(
            self.request(
                receiver,
                body=valid_payload,
                headers=self.authorized_headers(),
            )[0],
            409,
        )

    def test_sensitive_values_never_enter_response_or_status(self):
        receiver = self.make_receiver()
        statuses = []
        receiver.status_changed.connect(lambda state, detail: statuses.append((state, detail)))
        receiver.start()
        receiver.set_accepting(True)
        raw_secret = "private-raw-message"
        authorization_secret = "Bearer private-authorization"

        status, _, response = self.request(
            receiver,
            body=raw_secret.encode("utf-8"),
            headers={
                "Authorization": authorization_secret,
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(status, 401)
        exposed_text = json.dumps(response, ensure_ascii=False) + repr(statuses)
        for secret in ("test-token", raw_secret, authorization_secret):
            self.assertNotIn(secret, exposed_text)

    def test_non_post_method_is_rejected(self):
        receiver = self.make_receiver()
        receiver.start()

        status, _, response = self.request(
            receiver,
            method="GET",
            body=None,
            headers=self.authorized_headers(),
        )

        self.assertEqual(status, 405)
        self.assertEqual(response["code"], "METHOD_NOT_ALLOWED")

    def test_stop_closes_thread_and_only_deletes_own_descriptor(self):
        receiver = self.make_receiver()
        receiver.start()
        server_thread = receiver._thread

        receiver.stop()

        self.assertFalse(server_thread.is_alive())
        self.assertIsNone(receiver.server_address)
        self.assertFalse(self.runtime_path.exists())
        receiver.stop()

        replacement_receiver = self.make_receiver(token_factory=lambda: "first-token")
        replacement_receiver.start()
        replacement_thread = replacement_receiver._thread
        replacement = RuntimeDescriptor(
            port=replacement_receiver.server_address[1],
            token="replacement-token",
            pid=os.getpid(),
            protocol_version=1,
            expires_at=self.now + timedelta(seconds=RUNTIME_TTL_SECONDS),
        )
        write_runtime_descriptor(self.runtime_path, replacement)

        replacement_receiver.stop()

        self.assertFalse(replacement_thread.is_alive())
        self.assertTrue(self.runtime_path.exists())
        self.assertEqual(
            read_runtime_descriptor(
                self.runtime_path,
                now=lambda: self.now,
                pid_checker=lambda _pid: True,
            ).token,
            "replacement-token",
        )

    def test_stop_deletes_own_descriptor_after_its_discovery_ttl(self):
        receiver = self.make_receiver()
        receiver.start()
        self.now += timedelta(seconds=RUNTIME_TTL_SECONDS + 1)

        receiver.stop()

        self.assertFalse(self.runtime_path.exists())


if __name__ == "__main__":
    unittest.main()
