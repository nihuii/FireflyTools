import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.video_crawler.adapters.hls import HlsAdapter
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode


TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)


class HlsStructuredErrorTests(unittest.TestCase):
    def test_hls_ffmpeg_failure_uses_structured_code(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            segment_path = os.path.join(temp_dir, "00000.ts")
            output_path = os.path.join(temp_dir, "output.mp4")
            with open(segment_path, "wb") as segment:
                segment.write(b"segment")
            adapter = HlsAdapter(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
            )
            ffmpeg_error = subprocess.CalledProcessError(
                1,
                ["ffmpeg"],
                stderr=b"mux failed",
            )

            with patch(
                "tools.video_crawler.adapters.hls.subprocess.run",
                side_effect=ffmpeg_error,
            ):
                with self.assertRaises(VideoDownloadError) as raised:
                    adapter.merge_with_ffmpeg([segment_path], output_path)

        self.assertEqual(raised.exception.code, VideoErrorCode.FFMPEG_FAILED)
        self.assertFalse(raised.exception.retryable)

    def test_hls_fmp4_repair_failure_uses_structured_code(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
            init_path = os.path.join(temp_dir, "init.mp4")
            segment_path = os.path.join(temp_dir, "00000.m4s")
            output_path = os.path.join(temp_dir, "output.mp4")
            with open(init_path, "wb") as init_file:
                init_file.write(b"init")
            with open(segment_path, "wb") as segment:
                segment.write(b"segment")
            adapter = HlsAdapter(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
            )
            ffmpeg_error = subprocess.CalledProcessError(
                1,
                ["ffmpeg"],
                stderr=b"repair failed",
            )

            with patch(
                "tools.video_crawler.adapters.hls.subprocess.run",
                side_effect=ffmpeg_error,
            ):
                with self.assertRaises(VideoDownloadError) as raised:
                    adapter.merge_with_ffmpeg([segment_path], output_path, init_path)

        self.assertEqual(raised.exception.code, VideoErrorCode.FFMPEG_FAILED)
        self.assertFalse(raised.exception.retryable)


if __name__ == "__main__":
    unittest.main()
