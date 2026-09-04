import io
import json
import struct
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError

from tests.edge_companion_fixtures import valid_edge_message
from tools.edge_companion.native_host import (
    ALLOWED_EXTENSION_ORIGIN,
    MAX_MESSAGE_BYTES,
    main,
    read_native_message,
    run_host,
    write_native_message,
)
from tools.edge_companion.protocol import (
    EdgeProtocolError,
    parse_candidate_json,
    serialize_candidate,
)
from tools.edge_companion.runtime import RuntimeDescriptor


def native_frame(message):
    body = json.dumps(
        message, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return struct.pack("=I", len(body)) + body


def decoded_frames(raw):
    messages = []
    stream = io.BytesIO(raw)
    while True:
        prefix = stream.read(4)
        if not prefix:
            return messages
        if len(prefix) != 4:
            raise AssertionError("host wrote a truncated native-message prefix")
        length = struct.unpack("=I", prefix)[0]
        body = stream.read(length)
        if len(body) != length:
            raise AssertionError("host wrote a truncated native-message body")
        messages.append(json.loads(body.decode("utf-8")))


class FlushTrackingBytesIO(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.flush_count = 0

    def flush(self):
        self.flush_count += 1
        super().flush()


class FakeResponse:
    def __init__(self, status):
        self.status = status
        self.closed = False

    def close(self):
        self.closed = True


class NativeMessageFramingTests(unittest.TestCase):
    def assert_protocol_error(self, expected_code, operation):
        with self.assertRaises(EdgeProtocolError) as raised:
            operation()
        self.assertEqual(raised.exception.code, expected_code)

    def test_round_trip_uses_native_standard_32_bit_length_prefix(self):
        payload = {"protocol_version": 1, "type": "ping", "request_id": "r1"}
        stream = io.BytesIO()

        write_native_message(stream, payload)

        raw = stream.getvalue()
        expected_body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(raw[:4], struct.pack("=I", len(expected_body)))
        stream.seek(0)
        self.assertEqual(read_native_message(stream), payload)

    def test_clean_eof_returns_none(self):
        self.assertIsNone(read_native_message(io.BytesIO()))

    def test_truncated_prefix_is_rejected(self):
        self.assert_protocol_error(
            "INVALID_FRAME", lambda: read_native_message(io.BytesIO(b"\x01\x00"))
        )

    def test_truncated_body_is_rejected(self):
        stream = io.BytesIO(struct.pack("=I", 5) + b"{}")
        self.assert_protocol_error(
            "INVALID_FRAME", lambda: read_native_message(stream)
        )

    def test_invalid_utf8_is_wrapped(self):
        stream = io.BytesIO(struct.pack("=I", 1) + b"\xff")
        self.assert_protocol_error("INVALID_JSON", lambda: read_native_message(stream))

    def test_invalid_json_is_wrapped(self):
        body = b"{"
        stream = io.BytesIO(struct.pack("=I", len(body)) + body)
        self.assert_protocol_error("INVALID_JSON", lambda: read_native_message(stream))

    def test_non_object_json_is_rejected(self):
        stream = io.BytesIO(native_frame(["not", "an", "object"]))
        self.assert_protocol_error(
            "INVALID_MESSAGE", lambda: read_native_message(stream)
        )

    def test_announced_message_over_limit_is_rejected_before_body_read(self):
        stream = io.BytesIO(struct.pack("=I", MAX_MESSAGE_BYTES + 1))
        self.assert_protocol_error(
            "MESSAGE_TOO_LARGE", lambda: read_native_message(stream)
        )
        self.assertEqual(stream.tell(), 4)

    def test_writer_rejects_oversized_message_and_response(self):
        for label, message in (
            ("message", {"value": "x" * MAX_MESSAGE_BYTES}),
            (
                "response",
                {
                    "type": "ack",
                    "request_id": "r1",
                    "ok": False,
                    "code": "x" * MAX_MESSAGE_BYTES,
                },
            ),
        ):
            with self.subTest(label=label):
                self.assert_protocol_error(
                    "MESSAGE_TOO_LARGE",
                    lambda message=message: write_native_message(
                        io.BytesIO(), message
                    ),
                )

    def test_writer_wraps_type_encoding_and_recursion_errors(self):
        recursive = {}
        recursive["self"] = recursive
        for label, message, expected_code in (
            ("type", ["not-an-object"], "INVALID_MESSAGE"),
            ("encoding", {"value": "\ud800"}, "INVALID_JSON"),
            ("unserializable", {"value": object()}, "INVALID_MESSAGE"),
            ("recursive", recursive, "INVALID_MESSAGE"),
        ):
            with self.subTest(label=label):
                self.assert_protocol_error(
                    expected_code,
                    lambda message=message: write_native_message(
                        io.BytesIO(), message
                    ),
                )

    def test_writer_flushes_after_one_complete_frame(self):
        stream = FlushTrackingBytesIO()
        write_native_message(stream, {"type": "ack"})
        self.assertEqual(stream.flush_count, 1)


class NativeHostForwardingTests(unittest.TestCase):
    def setUp(self):
        self.runtime_path = Path("unused-runtime-descriptor.json")
        self.descriptor = RuntimeDescriptor(
            port=43123,
            token="runtime-private-token",
            pid=7654,
            protocol_version=1,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    def run_messages(
        self,
        messages,
        *,
        caller_origin=ALLOWED_EXTENSION_ORIGIN,
        descriptor_reader=None,
        pid_checker=lambda _pid: True,
        urlopen=None,
    ):
        stdin = io.BytesIO(b"".join(native_frame(message) for message in messages))
        stdout = io.BytesIO()
        stderr = io.StringIO()

        if descriptor_reader is None:
            descriptor = self.descriptor

            def descriptor_reader(path, *, pid_checker):
                if not pid_checker(descriptor.pid):
                    raise ValueError("dead process")
                return descriptor

        if urlopen is None:
            urlopen = lambda _request, timeout: FakeResponse(202)

        exit_code = run_host(
            caller_origin,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            runtime_path=self.runtime_path,
            descriptor_reader=descriptor_reader,
            pid_checker=pid_checker,
            urlopen=urlopen,
            timeout=0.25,
        )
        return exit_code, decoded_frames(stdout.getvalue()), stderr.getvalue()

    def test_only_allowed_extension_origin_can_reach_runtime_or_network(self):
        calls = []

        def forbidden_call(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("rejected origin reached an external dependency")

        secret_origin = "chrome-extension://evil/?token=origin-private-secret"
        exit_code, frames, diagnostics = self.run_messages(
            [valid_edge_message()],
            caller_origin=secret_origin,
            descriptor_reader=forbidden_call,
            urlopen=forbidden_call,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [])
        self.assertEqual(
            frames,
            [
                {
                    "type": "ack",
                    "request_id": valid_edge_message()["request_id"],
                    "ok": False,
                    "code": "ORIGIN_NOT_ALLOWED",
                }
            ],
        )
        self.assertNotIn(secret_origin, diagnostics)
        self.assertNotIn("origin-private-secret", diagnostics)

    def test_valid_candidate_posts_only_to_authenticated_loopback_endpoint(self):
        message = valid_edge_message()
        captured = {}

        def descriptor_reader(path, *, pid_checker):
            captured["runtime_path"] = path
            captured["pid_alive"] = pid_checker(self.descriptor.pid)
            return self.descriptor

        response = FakeResponse(202)

        def urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return response

        exit_code, frames, diagnostics = self.run_messages(
            [message],
            descriptor_reader=descriptor_reader,
            urlopen=urlopen,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(diagnostics, "")
        self.assertEqual(captured["runtime_path"], self.runtime_path)
        self.assertTrue(captured["pid_alive"])
        request = captured["request"]
        self.assertEqual(
            request.full_url, "http://127.0.0.1:43123/v1/candidate"
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer runtime-private-token",
        )
        expected_body = json.dumps(
            serialize_candidate(
                parse_candidate_json(
                    json.dumps(message, ensure_ascii=False, separators=(",", ":"))
                )
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(request.data, expected_body)
        self.assertEqual(captured["timeout"], 0.25)
        self.assertTrue(response.closed)
        self.assertEqual(
            frames,
            [
                {
                    "type": "ack",
                    "request_id": message["request_id"],
                    "ok": True,
                    "code": "ACCEPTED",
                }
            ],
        )

    def test_each_input_frame_is_validated_forwarded_and_acknowledged(self):
        first = valid_edge_message()
        second = valid_edge_message()
        second["request_id"] = "request-two"
        posted_request_ids = []
        descriptor_reads = []

        def descriptor_reader(path, *, pid_checker):
            descriptor_reads.append(path)
            return self.descriptor

        def urlopen(request, timeout):
            posted_request_ids.append(json.loads(request.data)["request_id"])
            return FakeResponse(202)

        _, frames, _ = self.run_messages(
            [first, second],
            descriptor_reader=descriptor_reader,
            urlopen=urlopen,
        )

        self.assertEqual(descriptor_reads, [self.runtime_path, self.runtime_path])
        self.assertEqual(posted_request_ids, [first["request_id"], "request-two"])
        self.assertEqual(
            [(frame["request_id"], frame["code"]) for frame in frames],
            [(first["request_id"], "ACCEPTED"), ("request-two", "ACCEPTED")],
        )

    def test_invalid_candidate_is_rejected_before_runtime_or_network(self):
        message = valid_edge_message()
        message["candidate"]["headers"]["Authorization"] = "private-auth"
        calls = []

        def forbidden_call(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("invalid candidate reached external dependency")

        _, frames, diagnostics = self.run_messages(
            [message], descriptor_reader=forbidden_call, urlopen=forbidden_call
        )

        self.assertEqual(calls, [])
        self.assertEqual(frames[0]["request_id"], message["request_id"])
        self.assertFalse(frames[0]["ok"])
        self.assertEqual(frames[0]["code"], "INVALID_HEADERS")
        exposed = json.dumps(frames, ensure_ascii=False) + diagnostics
        self.assertNotIn("private-auth", exposed)
        self.assertNotIn(message["candidate"]["url"], exposed)

    def test_unavailable_runtime_states_map_to_app_not_running_without_forward(self):
        for label, error, pid_checker in (
            ("missing", FileNotFoundError("descriptor missing"), lambda _pid: True),
            ("stale", ValueError("descriptor expired"), lambda _pid: True),
            ("read", RuntimeError("descriptor unreadable"), lambda _pid: True),
            ("dead-pid", None, lambda _pid: False),
        ):
            network_calls = []

            def descriptor_reader(path, *, pid_checker, error=error):
                if error is not None:
                    raise error
                if not pid_checker(self.descriptor.pid):
                    raise ValueError("dead process")
                return self.descriptor

            def urlopen(*args, **kwargs):
                network_calls.append((args, kwargs))
                raise AssertionError("unavailable app was forwarded to")

            with self.subTest(label=label):
                _, frames, _ = self.run_messages(
                    [valid_edge_message()],
                    descriptor_reader=descriptor_reader,
                    pid_checker=pid_checker,
                    urlopen=urlopen,
                )
                self.assertEqual(network_calls, [])
                self.assertFalse(frames[0]["ok"])
                self.assertEqual(frames[0]["code"], "APP_NOT_RUNNING")

    def test_receiver_statuses_map_to_stable_non_sensitive_ack_codes(self):
        expected = {
            202: (True, "ACCEPTED"),
            409: (False, "APP_NOT_WAITING"),
            401: (False, "UNAUTHORIZED"),
            403: (False, "FORBIDDEN"),
            413: (False, "MESSAGE_TOO_LARGE"),
            415: (False, "UNSUPPORTED_MEDIA_TYPE"),
            429: (False, "AUTH_RATE_LIMITED"),
        }
        for status, (ok, code) in expected.items():
            with self.subTest(status=status):
                _, frames, _ = self.run_messages(
                    [valid_edge_message()],
                    urlopen=lambda _request, timeout, status=status: FakeResponse(
                        status
                    ),
                )
                self.assertEqual(frames[0]["ok"], ok)
                self.assertEqual(frames[0]["code"], code)

    def test_url_error_is_structured_and_all_outputs_are_redacted(self):
        message = valid_edge_message()
        candidate_secret = "opaque"
        network_secret = "network-private-secret"

        def urlopen(_request, timeout):
            raise URLError(
                "Authorization: Bearer runtime-private-token "
                f"https://receiver.invalid/?token={network_secret}"
            )

        _, frames, diagnostics = self.run_messages([message], urlopen=urlopen)

        self.assertFalse(frames[0]["ok"])
        self.assertEqual(frames[0]["code"], "APP_NOT_RUNNING")
        exposed = json.dumps(frames, ensure_ascii=False) + diagnostics
        for secret in (
            "runtime-private-token",
            candidate_secret,
            network_secret,
            "Authorization: Bearer",
        ):
            self.assertNotIn(secret, exposed)
        self.assertIn("<redacted>", diagnostics)

    def test_receiver_error_body_is_never_read_or_reflected(self):
        private_body = "receiver-private-body"

        class BodyGuardResponse(FakeResponse):
            def read(self):
                raise AssertionError(private_body)

        _, frames, diagnostics = self.run_messages(
            [valid_edge_message()],
            urlopen=lambda _request, timeout: BodyGuardResponse(409),
        )

        exposed = json.dumps(frames, ensure_ascii=False) + diagnostics
        self.assertEqual(frames[0]["code"], "APP_NOT_WAITING")
        self.assertNotIn(private_body, exposed)


class NativeHostMainTests(unittest.TestCase):
    def test_main_accepts_origin_and_ignores_one_parent_window_argument(self):
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.StringIO()

        exit_code = main(
            [ALLOWED_EXTENSION_ORIGIN, "--parent-window=12345"],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            runtime_path=Path("unused.json"),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), b"")
        self.assertEqual(stderr.getvalue(), "")

    def test_main_safely_rejects_missing_and_unknown_arguments(self):
        for label, arguments in (
            ("missing", []),
            ("unknown", [ALLOWED_EXTENSION_ORIGIN, "--unexpected"]),
            (
                "multiple-parent-windows",
                [
                    ALLOWED_EXTENSION_ORIGIN,
                    "--parent-window=1",
                    "--parent-window=2",
                ],
            ),
        ):
            stdout = io.BytesIO()
            stderr = io.StringIO()
            with self.subTest(label=label):
                exit_code = main(
                    arguments,
                    stdin=io.BytesIO(),
                    stdout=stdout,
                    stderr=stderr,
                    runtime_path=Path("unused.json"),
                )
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout.getvalue(), b"")
                self.assertNotEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
