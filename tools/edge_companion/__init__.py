"""Public helpers for validating Edge companion media candidates."""

from tools.edge_companion.protocol import (
    EdgeProtocolError,
    candidate_from_task_payload,
    parse_candidate_json,
    serialize_candidate,
)

__all__ = [
    "EdgeProtocolError",
    "candidate_from_task_payload",
    "parse_candidate_json",
    "serialize_candidate",
]
