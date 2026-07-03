"""公开视频爬虫包的核心异常类型。"""

from tools.video_crawler.errors import VideoDownloadError, VideoErrorCode

__all__ = ["VideoDownloadError", "VideoErrorCode"]
