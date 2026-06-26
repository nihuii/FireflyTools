import glob
import os
import shutil
import subprocess
from collections.abc import Callable

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode


class YtDlpAdapter:
    name = "yt-dlp"
    priority = 10

    def __init__(
        self,
        enabled: bool = False,
        *,
        output_dir: str = "./downloads",
        log_callback: Callable[[str], None] | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ):
        self.enabled = enabled
        self.output_dir = output_dir
        self.log_callback = log_callback
        self.runner = runner or subprocess.run

    def is_available(self) -> bool:
        return self.enabled and shutil.which("yt-dlp") is not None

    def log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def download(self, url: str, output_filename: str) -> str:
        if not self.is_available():
            raise VideoDownloadError(
                VideoErrorCode.UNKNOWN,
                "yt-dlp 未启用或未安装，无法使用外部后备引擎",
                retryable=False,
            )

        os.makedirs(self.output_dir, exist_ok=True)
        output_template = os.path.join(self.output_dir, f"{output_filename}.%(ext)s")
        command = self._build_command(url, output_template)

        self.log("[yt-dlp] 使用外部引擎下载公开页面")
        try:
            self.runner(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise VideoDownloadError(
                VideoErrorCode.UNKNOWN,
                f"yt-dlp 下载失败，退出码 {exc.returncode}",
                retryable=False,
            ) from exc
        except OSError as exc:
            raise VideoDownloadError(
                VideoErrorCode.UNKNOWN,
                f"yt-dlp 启动失败: {exc.__class__.__name__}",
                retryable=False,
            ) from exc

        output_path = self._find_output_path(output_filename)
        if not output_path:
            raise VideoDownloadError(
                VideoErrorCode.EMPTY_OUTPUT,
                "yt-dlp 未生成有效输出文件",
                retryable=False,
            )
        return output_path

    @staticmethod
    def _build_command(url: str, output_template: str) -> list[str]:
        return [
            "yt-dlp",
            "--no-playlist",
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            url,
        ]

    def _find_output_path(self, output_filename: str) -> str:
        exact_mp4 = os.path.join(self.output_dir, f"{output_filename}.mp4")
        if self._is_nonempty_file(exact_mp4):
            return exact_mp4

        candidates = [
            path
            for path in glob.glob(os.path.join(self.output_dir, f"{output_filename}.*"))
            if self._is_nonempty_file(path)
        ]
        if not candidates:
            return ""
        return max(candidates, key=os.path.getmtime)

    @staticmethod
    def _is_nonempty_file(path: str) -> bool:
        return os.path.isfile(path) and os.path.getsize(path) > 0
