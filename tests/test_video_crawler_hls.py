import unittest

from tools.video_crawler.adapters.hls import (
    derive_hls_iv,
    group_fmp4_segments,
    ordered_ts_segments,
    parse_hls_byterange,
)


class HlsAdapterTests(unittest.TestCase):
    def test_explicit_iv_is_used(self):
        iv = derive_hls_iv("0x0000000000000000000000000000002a", media_sequence=7)

        self.assertEqual(iv, (42).to_bytes(16, "big"))

    def test_missing_iv_uses_media_sequence_number(self):
        iv = derive_hls_iv(None, media_sequence=7)

        self.assertEqual(iv, (7).to_bytes(16, "big"))

    def test_byterange_with_offset(self):
        self.assertEqual(parse_hls_byterange("1000@500"), "bytes=500-1499")

    def test_byterange_without_offset_uses_previous_end(self):
        self.assertEqual(parse_hls_byterange("1000", previous_end=499), "bytes=500-1499")

    def test_missing_byterange_returns_none(self):
        self.assertIsNone(parse_hls_byterange(None))

    def test_multiple_init_maps_create_multiple_merge_groups(self):
        items = [
            {"save_path": "00000.m4s", "init_map_url": "init-a.mp4"},
            {"save_path": "00001.m4s", "init_map_url": "init-a.mp4"},
            {"save_path": "00002.m4s", "init_map_url": "init-b.mp4"},
        ]

        groups = group_fmp4_segments(items)

        self.assertEqual(
            groups,
            [
                {"init_map_url": "init-a.mp4", "segments": ["00000.m4s", "00001.m4s"]},
                {"init_map_url": "init-b.mp4", "segments": ["00002.m4s"]},
            ],
        )

    def test_discontinuity_starts_new_fmp4_group(self):
        items = [
            {"save_path": "00000.m4s", "init_map_url": "init-a.mp4"},
            {"save_path": "00001.m4s", "init_map_url": "init-a.mp4", "discontinuity": True},
        ]

        groups = group_fmp4_segments(items)

        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["segments"], ["00000.m4s"])
        self.assertEqual(groups[1]["segments"], ["00001.m4s"])

    def test_ts_discontinuity_keeps_original_order(self):
        items = [
            {"save_path": "00000.ts"},
            {"save_path": "00001.ts", "discontinuity": True},
            {"save_path": "00002.ts"},
        ]

        self.assertEqual(
            ordered_ts_segments(items),
            ["00000.ts", "00001.ts", "00002.ts"],
        )


if __name__ == "__main__":
    unittest.main()
