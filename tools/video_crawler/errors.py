"""定义视频下载流程使用的结构化错误码和异常。"""

from enum import Enum


class VideoErrorCode(str, Enum):
    """枚举可向 UI 和批处理结果稳定传递的错误类别。"""
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
    EDGE_CANDIDATE_INVALID = "EDGE_CANDIDATE_INVALID"
    EDGE_CANDIDATE_EXPIRED = "EDGE_CANDIDATE_EXPIRED"


class VideoDownloadError(RuntimeError):
    """视频任务失败，带结构化错误码和用户可读消息。"""

    def __init__(self, code_or_message, message=None, *, details=None, retryable=False):
        """创建结构化异常，并兼容只传字符串的旧调用方式。

        Args:
            code_or_message: `VideoErrorCode`，或旧接口传入的错误字符串。
            message: 面向用户的简洁说明；省略时使用错误码文本。
            details: 仅供诊断的结构化上下文，不应直接泄漏敏感 Header。
            retryable: UI 是否可以安全建议用户按原配置重试。
        """
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
