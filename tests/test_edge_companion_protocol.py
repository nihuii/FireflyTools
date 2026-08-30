from datetime import datetime, timezone
import json
import sys
import unittest

from tests.edge_companion_fixtures import valid_edge_message
from tools.edge_companion.protocol import (
    EdgeProtocolError,
    candidate_from_task_payload,
    parse_candidate_json,
    serialize_candidate,
)
from tools.video_crawler.models import MediaKind


class EdgeCompanionProtocolTests(unittest.TestCase):
    def test_valid_v1_message_round_trips_without_sensitive_headers(self):
        candidate = parse_candidate_json(json.dumps(valid_edge_message()))
        self.assertEqual(candidate.kind, MediaKind.HLS)
        self.assertEqual(candidate.media_url, valid_edge_message()["candidate"]["url"])
        self.assertEqual(candidate.headers["User-Agent"], "Edge UA")
        self.assertEqual(serialize_candidate(candidate), valid_edge_message())

    def test_rejects_sensitive_or_injected_headers(self):
        for name, value in (
            ("Cookie", "sid=secret"),
            ("Authorization", "Bearer secret"),
            ("Referer", "https://example.test/\r\nX-Evil: 1"),
        ):
            message = valid_edge_message()
            message["candidate"]["headers"] = {name: value}
            with self.subTest(name=name):
                with self.assertRaises(EdgeProtocolError):
                    parse_candidate_json(json.dumps(message))

    def test_rejects_non_http_url_wrong_version_and_oversized_json(self):
        message = valid_edge_message()
        message["candidate"]["url"] = "blob:https://example.test/id"
        with self.assertRaises(EdgeProtocolError):
            parse_candidate_json(json.dumps(message))
        message = valid_edge_message()
        message["protocol_version"] = 2
        with self.assertRaises(EdgeProtocolError):
            parse_candidate_json(json.dumps(message))
        with self.assertRaises(EdgeProtocolError):
            parse_candidate_json("x" * (256 * 1024 + 1))

    def test_wraps_malformed_http_url_as_invalid_url(self):
        message = valid_edge_message()
        message["candidate"]["url"] = "http://[::1"

        with self.assertRaises(EdgeProtocolError) as caught:
            parse_candidate_json(json.dumps(message))

        self.assertEqual(caught.exception.code, "INVALID_URL")

    def test_rejects_control_characters_in_page_url(self):
        for label, suffix in (
            ("nul", "\x00"),
            ("crlf", "\r\nX-Evil: 1"),
            ("unit separator", "\x1f"),
            ("del", "\x7f"),
        ):
            message = valid_edge_message()
            message["page"]["url"] += suffix
            with self.subTest(label=label):
                with self.assertRaises(EdgeProtocolError) as caught:
                    parse_candidate_json(json.dumps(message))
                self.assertEqual(caught.exception.code, "INVALID_URL")

    def test_rejects_empty_hostname_and_invalid_ports(self):
        for url in (
            "http://:80/watch",
            "http://example.test:bad/watch",
            "http://example.test:65536/watch",
        ):
            message = valid_edge_message()
            message["candidate"]["url"] = url
            with self.subTest(url=url):
                with self.assertRaises(EdgeProtocolError) as caught:
                    parse_candidate_json(json.dumps(message))
                self.assertEqual(caught.exception.code, "INVALID_URL")

    def test_task_payload_is_revalidated_and_expiry_is_reported(self):
        candidate = parse_candidate_json(json.dumps(valid_edge_message()))
        task_payload = serialize_candidate(candidate)
        restored = candidate_from_task_payload(task_payload)
        self.assertFalse(
            restored.is_expired(datetime(2026, 8, 30, 12, 4, 59, tzinfo=timezone.utc))
        )
        self.assertTrue(
            restored.is_expired(datetime(2026, 8, 30, 12, 5, 1, tzinfo=timezone.utc))
        )

    def test_task_payload_rejects_tampered_critical_fields(self):
        tampering_cases = (
            (
                "method",
                lambda payload: payload["candidate"].__setitem__("method", "POST"),
                "INVALID_METHOD",
            ),
            (
                "cookie",
                lambda payload: payload["candidate"].__setitem__(
                    "headers", {"Cookie": "sid=secret"}
                ),
                "INVALID_HEADERS",
            ),
            (
                "version",
                lambda payload: payload.__setitem__("protocol_version", 2),
                "INVALID_VERSION",
            ),
            (
                "url",
                lambda payload: payload["candidate"].__setitem__(
                    "url", "blob:https://example.test/id"
                ),
                "INVALID_URL",
            ),
        )

        for label, tamper, expected_code in tampering_cases:
            candidate = parse_candidate_json(json.dumps(valid_edge_message()))
            task_payload = serialize_candidate(candidate)
            tamper(task_payload)
            with self.subTest(label=label):
                with self.assertRaises(EdgeProtocolError) as caught:
                    candidate_from_task_payload(task_payload)
                self.assertEqual(caught.exception.code, expected_code)

    def test_rejects_timestamps_that_overflow_utc_or_capture_ttl(self):
        for captured_at in (
            "9999-12-31T23:59:59-23:59",
            "9999-12-31T23:59:59Z",
        ):
            message = valid_edge_message()
            message["captured_at"] = captured_at
            with self.subTest(captured_at=captured_at):
                with self.assertRaises(EdgeProtocolError) as caught:
                    parse_candidate_json(json.dumps(message))
                self.assertEqual(caught.exception.code, "INVALID_TIMESTAMP")

    def test_wraps_deep_json_recursion_as_invalid_json(self):
        depth = max(3000, sys.getrecursionlimit() * 3)
        deeply_nested_json = "[" * depth + "0" + "]" * depth

        with self.assertRaises(EdgeProtocolError) as caught:
            parse_candidate_json(deeply_nested_json)

        self.assertEqual(caught.exception.code, "INVALID_JSON")

    def test_wraps_deep_task_mapping_recursion_as_invalid_message(self):
        message = valid_edge_message()
        deeply_nested = {}
        cursor = deeply_nested
        for _ in range(max(3000, sys.getrecursionlimit() * 3)):
            child = {}
            cursor["child"] = child
            cursor = child
        message["extra"] = deeply_nested

        with self.assertRaises(EdgeProtocolError) as caught:
            candidate_from_task_payload(message)

        self.assertEqual(caught.exception.code, "INVALID_MESSAGE")

    def test_wraps_unencodable_json_text_as_invalid_json(self):
        with self.assertRaises(EdgeProtocolError) as caught:
            parse_candidate_json('"\ud800"')

        self.assertEqual(caught.exception.code, "INVALID_JSON")

    def test_rejects_invalid_message_field_shapes_and_limits(self):
        invalid_values = (
            ("type", lambda message: message.__setitem__("type", "other")),
            (
                "sensitive flag",
                lambda message: message.__setitem__(
                    "sensitive_headers_included", True
                ),
            ),
            (
                "naive captured_at",
                lambda message: message.__setitem__(
                    "captured_at", "2026-08-30T12:00:00"
                ),
            ),
            (
                "long title",
                lambda message: message["page"].__setitem__("title", "x" * 513),
            ),
            (
                "method",
                lambda message: message["candidate"].__setitem__("method", "POST"),
            ),
            (
                "kind",
                lambda message: message["candidate"].__setitem__("kind", "drm"),
            ),
            (
                "unknown header",
                lambda message: message["candidate"].__setitem__(
                    "headers", {"X-Custom": "value"}
                ),
            ),
            (
                "field type",
                lambda message: message["candidate"].__setitem__(
                    "content_type", None
                ),
            ),
        )

        for label, mutate in invalid_values:
            message = valid_edge_message()
            mutate(message)
            with self.subTest(label=label):
                with self.assertRaises(EdgeProtocolError):
                    parse_candidate_json(json.dumps(message))


if __name__ == "__main__":
    unittest.main()
