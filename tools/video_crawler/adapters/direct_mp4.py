import os
from collections.abc import Callable

import requests

from tools.video_crawler.models import DiagnosticReport, MediaCandidate, MediaKind


class DirectMp4Adapter:
    name = "direct_mp4"
    priority = 100

    def __init__(
        self,
        *,
        output_dir: str,
        headers_getter: Callable[[], dict[str, str]],
        log_callback: Callable[[str], None] | None = None,
        download_url: Callable[[str, str], str] | None = None,
    ):
        self.output_dir = output_dir
        self.headers_getter = headers_getter
        self.log_callback = log_callback
        self._download_url_hook = download_url

    def log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def can_handle(self, candidate: MediaCandidate) -> bool:
        return candidate.kind == MediaKind.DIRECT_MP4

    def diagnose(self, url: str) -> DiagnosticReport:
        return DiagnosticReport(
            source_url=url,
            candidates=[
                MediaCandidate(
                    url=url,
                    kind=MediaKind.DIRECT_MP4,
                    source=self.name,
                    score=100,
                )
            ],
        )

    def download(self, candidate: MediaCandidate, output_filename: str) -> str:
        save_path = os.path.join(self.output_dir, f"{output_filename}.mp4")
        if self._download_url_hook:
            return self._download_url_hook(candidate.url, save_path)
        return self.download_url(candidate.url, save_path)

    def download_url(self, video_url: str, save_path: str) -> str:
        with requests.get(
            video_url,
            headers=self.headers_getter(),
            stream=True,
        ) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            last_percent = 0

            with open(save_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output_file.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = int((downloaded / total_size) * 100)
                        if percent - last_percent >= 10:
                            self.log(f"[>] MP4 下载进度: {percent}%")
                            last_percent = percent
        self.log(f"[+] MP4 下载完成: {save_path}")
        return save_path
