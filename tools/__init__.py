"""初始化 tools 包，并让子进程优先使用仓库内置的 FFmpeg。"""

from tools.runtime_setup import configure_bundled_ffmpeg


BUNDLED_FFMPEG = configure_bundled_ffmpeg()
