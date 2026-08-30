from datetime import datetime, timezone
import json
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
