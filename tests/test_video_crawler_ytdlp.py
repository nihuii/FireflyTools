import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.video_crawler.adapters.ytdlp import YtDlpAdapter
from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode


TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)


class YtDlpAdapterTests(unittest.TestCase):
    def test_adapter_disabled_when_executable_missing(self):
        with patch("tools.video_crawler.adapters.ytdlp.shutil.which", return_value=None):
            self.assertFalse(YtDlpAdapter(enabled=True).is_available())

    def test_adapter_requires_user_enabled_flag(self):
        with patch(
            "tools.video_crawler.adapters.ytdlp.shutil.which",
            return_value="yt-dlp",
        ):
            self.assertFalse(YtDlpAdapter(enabled=False).is_available())

    def test_download_runs_external_engine_without_logging_secrets(self):
        commands = []
        logs = []
        sensitive_url = "https://example.test/watch?token=secret&Authorization=bearer"

        def fake_runner(command, **kwargs):
            commands.append((command, kwargs))
            output_template = command[command.index("-o") + 1]
            output_path = output_template.replace("%(ext)s", "mp4")
            with open(output_path, "wb") as output_file:
                output_file.write(b"video")
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as output_dir:
            with patch(
                "tools.video_crawler.adapters.ytdlp.shutil.which",
                return_value="yt-dlp",
            ):
                adapter = YtDlpAdapter(
                    enabled=True,
                    output_dir=output_dir,
                    log_callback=logs.append,
                    runner=fake_runner,
                )

                result = adapter.download(sensitive_url, "public-video")

        self.assertEqual(result, os.path.join(output_dir, "public-video.mp4"))
        self.assertEqual(commands[0][0][-1], sensitive_url)
        self.assertIn("--no-playlist", commands[0][0])
        self.assertIn("yt-dlp", "\n".join(logs))
        self.assertNotIn("token=", "\n".join(logs))
        self.assertNotIn("secret", "\n".join(logs))
        self.assertNotIn("Authorization", "\n".join(logs))

    def test_subprocess_failure_becomes_non_retryable_unknown_error(self):
        def failing_runner(command, **kwargs):
            raise subprocess.CalledProcessError(1, command, stderr="boom")

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as output_dir:
            with patch(
                "tools.video_crawler.adapters.ytdlp.shutil.which",
                return_value="yt-dlp",
            ):
                adapter = YtDlpAdapter(
                    enabled=True,
                    output_dir=output_dir,
                    runner=failing_runner,
                )

                with self.assertRaises(VideoDownloadError) as raised:
                    adapter.download("https://example.test/watch", "failed")

        self.assertEqual(raised.exception.code, VideoErrorCode.UNKNOWN)
        self.assertFalse(raised.exception.retryable)
        self.assertIn("yt-dlp", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
