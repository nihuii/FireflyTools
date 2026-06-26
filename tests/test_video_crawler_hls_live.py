import os
import tempfile
import unittest
from types import SimpleNamespace

from tools.video_crawler.adapters.hls import HlsAdapter, is_live_playlist


def workspace_temp_dir():
    temp_root = os.path.join(os.getcwd(), "tests", ".tmp")
    os.makedirs(temp_root, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=temp_root)


class HlsLiveTests(unittest.TestCase):
    def test_detects_playlist_without_endlist_as_live(self):
        playlist = SimpleNamespace(is_endlist=False, playlist_type=None)

        self.assertTrue(is_live_playlist(playlist))

    def test_detects_vod_playlist_as_not_live(self):
        playlist = SimpleNamespace(is_endlist=True, playlist_type="VOD")

        self.assertFalse(is_live_playlist(playlist))


class HlsLiveDownloadTests(unittest.TestCase):
    def test_download_collected_live_segments_downloads_and_merges_segments(self):
        with workspace_temp_dir() as temp_dir:
            output_dir = os.path.join(temp_dir, "downloads")
            os.makedirs(output_dir)
            downloaded = []

            async def fake_download_ts(session, ts_url, save_path, cipher, extra_headers=None):
                downloaded.append((ts_url, os.path.basename(save_path)))
                with open(save_path, "wb") as segment_file:
                    segment_file.write(b"segment")
                return True

            def fake_merge(ts_files, output_mp4, init_file=None):
                self.assertEqual(
                    [os.path.basename(path) for path in ts_files],
                    ["00007.ts", "00008.ts"],
                )
                with open(output_mp4, "wb") as output_file:
                    output_file.write(b"video")
                return output_mp4

            adapter = HlsAdapter(
                output_dir=output_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
                download_ts=fake_download_ts,
                merge_with_ffmpeg=fake_merge,
                live_record_seconds=1,
            )
            captured_items = [
                (
                    7,
                    SimpleNamespace(
                        absolute_uri="https://cdn/live/7.ts",
                        key=None,
                        byterange=None,
                        init_section=None,
                        discontinuity=False,
                    ),
                ),
                (
                    8,
                    SimpleNamespace(
                        absolute_uri="https://cdn/live/8.ts",
                        key=None,
                        byterange=None,
                        init_section=None,
                        discontinuity=False,
                    ),
                ),
            ]

            result = adapter._run_async(
                adapter.download_collected_live_segments(captured_items, "live")
            )

            self.assertEqual(result, os.path.join(output_dir, "live.mp4"))
            self.assertEqual(
                downloaded,
                [
                    ("https://cdn/live/7.ts", "00007.ts"),
                    ("https://cdn/live/8.ts", "00008.ts"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
