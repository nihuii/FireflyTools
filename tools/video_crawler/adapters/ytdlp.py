"""封装可选的 yt-dlp 外部后备下载流程。"""

import glob
import os
import shutil
import subprocess
from collections.abc import Callable

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode


class YtDlpAdapter:
    """在用户明确启用时调用 yt-dlp 作为外部后备。"""
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
        """保存用户开关、输出目录和可替换的子进程执行器。

        `runner` 注入点让测试无需启动真实 yt-dlp；生产路径使用
        `subprocess.run`，且始终传递参数列表而不是 shell 字符串。
        """
        self.enabled = enabled
        self.output_dir = output_dir
        self.log_callback = log_callback
        self.runner = runner or subprocess.run

    def is_available(self) -> bool:
        """判断用户开关和系统 yt-dlp 可执行文件是否同时可用。"""
        return self.enabled and shutil.which("yt-dlp") is not None

    def log(self, message: str) -> None:
        """将 yt-dlp 后备下载状态转发给外部日志回调。"""
        if self.log_callback:
            self.log_callback(message)

    def download(self, url: str, output_filename: str) -> str:
        """调用 yt-dlp 下载公开页面，并返回实际生成的非空文件。"""
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
        """构造不经 shell 展开的 yt-dlp 参数列表。"""
        # --no-playlist 防止单个页面意外扩展成整张播放列表；统一请求 MP4
        # 合并格式，使返回路径与内置下载器的用户预期一致。
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
        """根据输出模板查找 yt-dlp 实际生成的非临时文件。"""
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
        """判断路径是否指向存在且非空的普通文件。"""
        return os.path.isfile(path) and os.path.getsize(path) > 0
