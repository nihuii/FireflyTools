from enum import Enum


class VideoErrorCode(str, Enum):
    UNKNOWN = "UNKNOWN"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    HTTP_FORBIDDEN = "HTTP_FORBIDDEN"
    HTTP_NOT_FOUND = "HTTP_NOT_FOUND"
    NO_MEDIA_FOUND = "NO_MEDIA_FOUND"
    UNSUPPORTED_DASH = "UNSUPPORTED_DASH"
    UNSUPPORTED_DRM = "UNSUPPORTED_DRM"
    M3U8_PARSE_FAILED = "M3U8_PARSE_FAILED"
    SEGMENT_FAILURE_RATE_EXCEEDED = "SEGMENT_FAILURE_RATE_EXCEEDED"
    FFMPEG_FAILED = "FFMPEG_FAILED"
    EMPTY_OUTPUT = "EMPTY_OUTPUT"


class VideoDownloadError(RuntimeError):
    """视频任务失败，带结构化错误码和用户可读消息。"""

    def __init__(self, code_or_message, message=None, *, details=None, retryable=False):
        if isinstance(code_or_message, VideoErrorCode):
            code = code_or_message
            final_message = message or code.value
        else:
            code = VideoErrorCode.UNKNOWN
            final_message = str(code_or_message)

        super().__init__(final_message)
        self.code = code
        self.details = details or {}
        self.retryable = retryable
